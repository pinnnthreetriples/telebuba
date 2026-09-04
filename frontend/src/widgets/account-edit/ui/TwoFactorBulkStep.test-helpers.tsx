import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import { vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { TwoFactorBulkStep } from './TwoFactorBulkStep';

// The harness both halves of the add-wizard's cloud-password step drive: the
// selection form and the sequential batch in TwoFactorBulkStep.test.tsx, the
// reveal-once password table in TwoFactorBulkResults.test.tsx. Split into a
// .test-helpers sibling for the 700-line test-source cap, the way
// TwoFactorSection's two halves already are.

export const IDS = ['acc-1', 'acc-2', 'acc-3'];

// What the wizard hands down as `sources`: the file each account came out of.
export const SOURCES: Record<string, string> = {
  'acc-1': 'one.session',
  'acc-2': 'two.session',
  'acc-3': 'three.session',
};

export const NAMES = ['Аня', 'Боря', 'Вера'];

export const ACCOUNTS: AccountRead[] = IDS.map((accountId, index) => ({
  account_id: accountId,
  status: 'new',
  created_at: 'n',
  updated_at: 'n',
  first_name: NAMES[index],
}));

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export function twofaPath(accountId: string): string {
  return `/api/v1/accounts/${accountId}/2fa`;
}

// POST <id>/2fa goes to `answer`, keyed by the account id in the path; the
// display-name list answers `accounts`, and anything else falls through to an
// empty page. Thunks, never Responses — a body can only be read once, and the
// batch hits the same route for every account.
export function stubBatch(
  answer: (accountId: string, request: Request) => Response | Promise<Response>,
  accounts: AccountRead[] | null = ACCOUNTS,
): void {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    const twofa = /^\/api\/v1\/accounts\/([^/]+)\/2fa$/.exec(pathname);
    if (twofa && request.method === 'POST') return Promise.resolve(answer(twofa[1] ?? '', request));
    if (pathname === '/api/v1/accounts') {
      // `null` accounts = the list is unavailable, so the rows fall back to raw ids.
      return accounts
        ? Promise.resolve(jsonResponse({ items: accounts, next_cursor: null }))
        : Promise.resolve(jsonResponse({ error: { code: 'server_error', message: 'nope' } }, 500));
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
}

// Every POST the batch sent, in the order it sent them.
export function twofaPosts(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter(
      (request) => request.method === 'POST' && new URL(request.url).pathname.endsWith('/2fa'),
    );
}

export async function twofaBodies(): Promise<{ accountId: string; body: unknown }[]> {
  return Promise.all(
    twofaPosts().map(async (request) => ({
      accountId: new URL(request.url).pathname.split('/')[4] ?? '',
      body: (await request.clone().json()) as unknown,
    })),
  );
}

// A response the test resolves itself, so "one at a time" is observable.
export function deferred(): {
  promise: Promise<Response>;
  resolve: (response: Response) => void;
} {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

export function renderStep(
  props: Partial<Parameters<typeof TwoFactorBulkStep>[0]> = {},
): { queryClient: QueryClient } & ReturnType<typeof render> {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <TwoFactorBulkStep
          accountIds={IDS}
          sources={SOURCES}
          onDone={vi.fn()}
          onImported={vi.fn()}
          {...props}
        />
      </QueryClientProvider>,
    ),
  };
}
