import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { vi } from 'vitest';

import type { WarmingAccountState, WarmingBoardState } from '@/shared/api';

export function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

export function account(
  id: string,
  state: WarmingAccountState['state'],
  readiness: WarmingAccountState['readiness'] = { ready: true, reasons: [] },
): WarmingAccountState {
  return { account_id: id, label: id, state, health: 'ok', cycles_completed: 1, readiness };
}

export const BOARD: WarmingBoardState = {
  idle: [
    account('idle-1', 'idle'),
    account('idle-2', 'idle', { ready: false, reasons: ['no proxy'] }),
  ],
  warming: [account('warm-1', 'active')],
  channels: { channels: [{ channel: '@news', created_at: 'now' }] },
  settings: {
    inter_account_chat: false,
    reactions_enabled: true,
    join_enabled: true,
    enforce_readiness: true,
    has_gemini_key: false,
    gemini_model: 'gemini-2.5-flash',
    updated_at: 'now',
  },
  channel_count: 1,
  active_count: 1,
  summary: {
    total: 2,
    warming: 1,
    active: 1,
    ready: 0,
    attention: 0,
    trust_healthy: 0,
    trust_watch: 0,
    trust_risk: 0,
  },
};

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export function routeApi() {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/warming/board') return Promise.resolve(jsonResponse(BOARD));
    return Promise.resolve(jsonResponse({}));
  });
}

export function lastEventSource(): { emit(data: unknown): void } | undefined {
  return (
    globalThis.EventSource as unknown as { last(): { emit(d: unknown): void } | undefined }
  ).last();
}

// Counts board refetches — the observable proof that a settled mutation's
// invalidate actually ran.
export function boardFetches(): number {
  return vi
    .mocked(fetch)
    .mock.calls.filter(
      ([input]) => new URL((input as Request).url).pathname === '/api/v1/warming/board',
    ).length;
}
