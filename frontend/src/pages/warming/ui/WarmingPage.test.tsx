import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { WarmingAccountState, WarmingBoardState } from '@/shared/api';

import { WarmingPage } from './WarmingPage';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function account(id: string, state: WarmingAccountState['state']): WarmingAccountState {
  return { account_id: id, label: id, state, health: 'ok', cycles_completed: 1 };
}

const BOARD: WarmingBoardState = {
  idle: [account('idle-1', 'idle')],
  warming: [account('warm-1', 'active')],
  channels: { channels: [{ channel: '@news', created_at: 'now' }] },
  settings: {
    inter_account_chat: false,
    reactions_enabled: true,
    join_enabled: true,
    enforce_readiness: true,
    quiet_hours_enabled: false,
    quiet_hours_start: 0,
    quiet_hours_end: 0,
    max_daily_actions: 0,
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

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function routeApi() {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/warming/board') return Promise.resolve(jsonResponse(BOARD));
    return Promise.resolve(jsonResponse({}));
  });
}

function lastEventSource(): { emit(data: unknown): void } | undefined {
  return (
    globalThis.EventSource as unknown as { last(): { emit(d: unknown): void } | undefined }
  ).last();
}

test('renders the board, channels and settings from live data', async () => {
  routeApi();
  renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('idle-1')).toBeInTheDocument();
  });
  expect(screen.getByText('warm-1')).toBeInTheDocument();
  expect(screen.getByText('@news')).toBeInTheDocument();
});

test('refetches the board on a live SSE event', async () => {
  routeApi();
  renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('idle-1')).toBeInTheDocument();
  });
  const boardCalls = () =>
    vi
      .mocked(fetch)
      .mock.calls.filter(([input]) => (input as Request).url.includes('/warming/board')).length;
  const before = boardCalls();
  act(() => {
    lastEventSource()?.emit({ id: 1, event: 'cycle_started' });
  });
  await waitFor(() => {
    expect(boardCalls()).toBeGreaterThan(before);
  });
});

test('starts an idle account', async () => {
  routeApi();
  renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('idle-1')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Запустить'));
  await waitFor(() => {
    const started = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/warming/start'));
    expect(started).toBe(true);
  });
});

test('adds a channel', async () => {
  routeApi();
  renderWithClient(<WarmingPage />);
  await waitFor(() => {
    expect(screen.getByText('@news')).toBeInTheDocument();
  });
  await userEvent.type(screen.getByLabelText(/Ссылки или/), '@more');
  await userEvent.click(screen.getByText('Добавить'));
  await waitFor(() => {
    const added = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.endsWith('/warming/channels'));
    expect(added).toBe(true);
  });
});
