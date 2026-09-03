import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, vi } from 'vitest';

import type { DiscoveryAccountOption, DiscoveryBoard, DiscoveryCandidate } from '@/shared/api';

import { ChannelDiscoveryModal } from './ChannelDiscoveryModal';

export type MockEventSourceCtor = {
  last: () => { emit: (payload: unknown) => void } | undefined;
};

// One premium eligible, one plain eligible, one busy: the premium default is `acc-p`
// alone, so every spec that just presses «Найти» posts a known account list.
export const ACCOUNTS: DiscoveryAccountOption[] = [
  { account_id: 'acc-p', name: 'Prem', premium: true, busy_reason: null },
  { account_id: 'acc-n', name: 'Plain', premium: false, busy_reason: null },
  { account_id: 'acc-b', name: 'Busy', premium: false, busy_reason: 'account_cooling' },
];

export function candidate(overrides: Partial<DiscoveryCandidate> = {}): DiscoveryCandidate {
  return {
    channel: 'alpha',
    title: 'Alpha',
    source: 'telegram_search',
    qualification: 'comments_on',
    ...overrides,
  };
}

export function boardPayload(
  candidates: DiscoveryCandidate[],
  progress: Partial<DiscoveryBoard['progress']> = {},
): DiscoveryBoard {
  return {
    campaign_id: 'c1',
    progress: {
      phase: 'done',
      running: false,
      total: candidates.length,
      qualified: candidates.length,
      comments_on: candidates.length,
      last_error: null,
      ...progress,
    },
    candidates,
  };
}

export function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function errorResponse(status: number, code: string, message: string): Response {
  return new Response(JSON.stringify({ error: { code, message } }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export type Routes = {
  board?: DiscoveryBoard;
  boards?: DiscoveryBoard[];
  startStatus?: string;
  refusedAccountId?: string;
  startFails?: boolean;
  // One status per requested channel, defaulting to all-linked. The server reports
  // per-channel outcomes, so a spec has to be able to mix them.
  adoptStatuses?: string[];
  adoptFails?: boolean;
  boardFailures?: number;
  accounts?: DiscoveryAccountOption[] | 'fail' | 'hang';
  // Mutable so a spec can stall the board mid-test; a stalled fetch proves a row on
  // screen came from the cache rather than the server.
  hang?: { board: boolean };
};

export function route(routes: Routes = {}) {
  const calls: { path: string; method: string; body: unknown }[] = [];
  const boards = routes.boards ?? (routes.board ? [routes.board] : [boardPayload([])]);
  let boardIndex = 0;
  let boardFailures = routes.boardFailures ?? 0;

  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const url = new URL(request.url);
    let body: unknown = null;
    if (request.method === 'POST') {
      body = JSON.parse(await request.clone().text());
    }
    calls.push({ path: url.pathname, method: request.method, body });

    if (url.pathname === '/api/v1/warming/settings') {
      return jsonResponse({
        inter_account_chat: false,
        reactions_enabled: true,
        gemini_model: 'gemini-2.5-flash',
        updated_at: 'now',
      });
    }
    if (url.pathname.endsWith('/discovery/accounts')) {
      if (routes.accounts === 'hang') return new Promise<Response>(() => undefined);
      if (routes.accounts === 'fail') return errorResponse(500, 'internal', 'boom');
      return jsonResponse({ items: routes.accounts ?? ACCOUNTS });
    }
    if (url.pathname.endsWith('/discovery/search')) {
      // What an inverted subscriber range actually returns.
      if (routes.startFails === true) return errorResponse(422, 'validation_error', 'members_min');
      return jsonResponse({
        status: routes.startStatus ?? 'started',
        refused_account_id: routes.refusedAccountId ?? null,
      });
    }
    if (url.pathname.endsWith('/discovery/adopt')) {
      if (routes.adoptFails === true) return errorResponse(500, 'internal', 'boom');
      const requested = (body as { channels?: string[] } | null)?.channels ?? [];
      return jsonResponse({
        outcomes: requested.map((channel, index) => ({
          status: routes.adoptStatuses?.[index] ?? 'linked',
          channel,
        })),
      });
    }
    if (url.pathname.endsWith('/discovery')) {
      if (routes.hang?.board === true) return new Promise<Response>(() => undefined);
      if (boardFailures > 0) {
        boardFailures -= 1;
        return errorResponse(500, 'internal', 'boom');
      }
      const payload = boards[Math.min(boardIndex, boards.length - 1)];
      boardIndex += 1;
      return jsonResponse(payload);
    }
    return jsonResponse({});
  });

  return calls;
}

export function newQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

export function renderModal(onClose = vi.fn(), queryClient = newQueryClient()) {
  return render(
    <QueryClientProvider client={queryClient}>
      <ChannelDiscoveryModal campaignId="c1" campaignName="Promo" onClose={onClose} />
    </QueryClientProvider>,
  );
}

export const submitButton = () => screen.getByRole('button', { name: 'Найти' });

export async function typeKeywords(text = 'crypto') {
  await userEvent.type(screen.getByPlaceholderText('крипта, трейдинг, новости'), text);
}

export async function startSearch() {
  await typeKeywords();
  // The account list arrives asynchronously and «Найти» stays dead until it does.
  await waitFor(() => {
    expect(submitButton()).toBeEnabled();
  });
  await userEvent.click(submitButton());
}
