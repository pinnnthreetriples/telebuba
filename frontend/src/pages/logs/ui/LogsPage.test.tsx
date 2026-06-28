import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { LogsPage } from './LogsPage';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function logRow(id: number, event: string, status = 'success') {
  return { id, created_at: 'now', level: 'INFO', status, account_id: 'acc-1', event, extra: {} };
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function routeLogs(page1: unknown, page2?: unknown) {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    const body = url.searchParams.get('cursor') ? (page2 ?? page1) : page1;
    return Promise.resolve(jsonResponse(body));
  });
}

interface MockSource {
  emit(data: unknown): void;
}
function lastEventSource(): MockSource | undefined {
  return (globalThis.EventSource as unknown as { last(): MockSource | undefined }).last();
}

test('renders log rows from the API', async () => {
  routeLogs({ items: [logRow(1, 'thing_happened')], next_cursor: null });
  renderWithClient(<LogsPage />);
  await waitFor(() => {
    expect(screen.getByText('thing_happened')).toBeInTheDocument();
  });
  expect(screen.getByText('acc-1')).toBeInTheDocument();
});

test('prepends a live SSE event to the newest page', async () => {
  routeLogs({ items: [logRow(1, 'first')], next_cursor: null });
  renderWithClient(<LogsPage />);
  await waitFor(() => {
    expect(screen.getByText('first')).toBeInTheDocument();
  });
  act(() => {
    lastEventSource()?.emit(logRow(2, 'live_event'));
  });
  await waitFor(() => {
    expect(screen.getByText('live_event')).toBeInTheDocument();
  });
});

test('shows the empty state', async () => {
  routeLogs({ items: [], next_cursor: null });
  renderWithClient(<LogsPage />);
  await waitFor(() => {
    expect(screen.getByText('Записей нет')).toBeInTheDocument();
  });
});

test('paginates forward', async () => {
  routeLogs(
    { items: [logRow(1, 'first')], next_cursor: '50' },
    { items: [logRow(2, 'second')], next_cursor: null },
  );
  renderWithClient(<LogsPage />);
  await waitFor(() => {
    expect(screen.getByText('first')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Вперёд'));
  await waitFor(() => {
    expect(screen.getByText('second')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Назад'));
  await waitFor(() => {
    expect(screen.getByText('first')).toBeInTheDocument();
  });
});

test('applies the status and account filters', async () => {
  routeLogs({ items: [logRow(1, 'bad', 'error')], next_cursor: null });
  renderWithClient(<LogsPage />);
  await waitFor(() => {
    expect(screen.getByText('bad')).toBeInTheDocument();
  });
  await userEvent.selectOptions(screen.getByLabelText('Статус'), 'error');
  await userEvent.type(screen.getByLabelText('Аккаунт'), 'acc-1');
  await waitFor(() => {
    const filtered = vi.mocked(fetch).mock.calls.some(([input]) => {
      const url = new URL((input as Request).url);
      return url.searchParams.get('status') === 'error';
    });
    expect(filtered).toBe(true);
  });
});
