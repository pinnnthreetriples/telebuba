import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead, TwoFactorStatusResult } from '@/shared/api';

import { TwoFactorSection } from './TwoFactorSection';

// The switchboard both 2FA test files drive the card through — the password half in
// TwoFactorSection.test.tsx, the recovery-email half in TwoFactorEmail.test.tsx. Split
// for the 700-line test-source cap, shared through a .test-helpers sibling the way
// ProfileModal's two halves already are.

export const ACCOUNT: AccountRead = {
  account_id: 'acc-1',
  label: 'Main',
  status: 'alive',
  phone: '+79051184490',
  created_at: 'now',
  updated_at: 'now',
};

export const TWOFA = '/api/v1/accounts/acc-1/2fa';
export const TITLE = 'Облачный пароль (2FA)';

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

// GET /2fa answers the live status plus whether OUR copy of the password exists.
export function viewResponse(
  status: TwoFactorStatusResult | null,
  hasStoredPassword = true,
  error: string | null = null,
): Response {
  return jsonResponse({ status, has_stored_password: hasStoredPassword, error });
}

// Anything the route does not answer falls through to an empty page (the
// accounts list / proxy list an invalidation refetches).
export function stubApi(route: (request: Request) => Response | undefined): void {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    return Promise.resolve(route(request) ?? jsonResponse({ items: [], next_cursor: null }));
  });
}

// The switch every test in here needs: GET /2fa answers `status`, and `routes`
// overrides one `"<METHOD> <pathname>"` at a time. Thunks, not Responses — a body
// can only be read once, and a refetch hits the same key again.
export function stubTwofa(
  status: TwoFactorStatusResult | null,
  {
    stored = true,
    error = null,
    routes = {},
  }: {
    stored?: boolean;
    error?: string | null;
    routes?: Record<string, () => Response>;
  } = {},
): void {
  stubApi((request) => {
    const { pathname } = new URL(request.url);
    const route = routes[`${request.method} ${pathname}`];
    if (route) return route();
    return pathname === TWOFA ? viewResponse(status, stored, error) : undefined;
  });
}

// 2FA on, no confirmed recovery address, one pending and waiting for its code.
export const PENDING: TwoFactorStatusResult = {
  has_password: true,
  has_recovery: false,
  email_unconfirmed_pattern: 'o**@example.com',
};

export function renderSection() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <TwoFactorSection account={ACCOUNT} />
      </QueryClientProvider>,
    ),
  };
}

// Every card is collapsed by default and a collapsed body is `hidden`, so the
// controls do not exist for a role query until the title is clicked.
export async function openCard(): Promise<void> {
  await userEvent.click(screen.getByText(TITLE));
}

export function requests(pathname: string, method?: string): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter(
      (request) =>
        new URL(request.url).pathname === pathname && (!method || request.method === method),
    );
}

export function urls(): string[] {
  return vi.mocked(fetch).mock.calls.map(([input]) => (input as Request).url);
}
