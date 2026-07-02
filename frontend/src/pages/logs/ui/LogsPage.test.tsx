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

function logRow(id: number, event: string, status = 'success', accountId: string | null = 'acc-1') {
  return { id, created_at: 'now', level: 'INFO', status, account_id: accountId, event, extra: {} };
}

// Two accounts: acc-1 has log rows, acc-2 never appears on any log page — it must
// still be selectable in the filter (fed from GET /accounts, not the page).
const ACCOUNTS = {
  items: [
    { account_id: 'acc-1', phone: '+79990001122', label: null, status: 'alive' },
    { account_id: 'acc-2', phone: '+79995554433', label: null, status: 'alive' },
  ],
  next_cursor: null,
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function routeLogs(page1: unknown, page2?: unknown) {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/accounts') {
      return Promise.resolve(jsonResponse(ACCOUNTS));
    }
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

test('renders log rows, localizing the event code and showing the account phone', async () => {
  // A known event code resolves to its label; the account column shows the phone,
  // not the raw account_id.
  routeLogs({ items: [logRow(1, 'warming_started')], next_cursor: null });
  renderWithClient(<LogsPage />);
  await waitFor(() => {
    expect(screen.getByText('Прогрев запущен')).toBeInTheDocument();
  });
  // The phone shows in the table cell (and also in the always-rendered dropdown),
  // and the raw session-stem id never appears.
  await waitFor(() => {
    expect(screen.getAllByText('+79990001122').length).toBeGreaterThan(0);
  });
  expect(screen.getByRole('cell', { name: '+79990001122' })).toBeInTheDocument();
  expect(screen.queryByText('acc-1')).not.toBeInTheDocument();
});

test('falls back to the raw code for an unknown event', async () => {
  routeLogs({ items: [logRow(1, 'totally_unknown_event')], next_cursor: null });
  renderWithClient(<LogsPage />);
  await waitFor(() => {
    expect(screen.getByText('totally_unknown_event')).toBeInTheDocument();
  });
});

test('the account filter lists all accounts, independent of the current log page', async () => {
  // Only acc-1 has rows; acc-2 must still be offered by the dropdown.
  routeLogs({ items: [logRow(1, 'warming_started')], next_cursor: null });
  renderWithClient(<LogsPage />);
  await waitFor(() => {
    expect(screen.getByText('Прогрев запущен')).toBeInTheDocument();
  });
  await waitFor(() => {
    // accounts query settled
    expect(screen.getAllByText('+79990001122').length).toBeGreaterThan(0);
  });
  await userEvent.click(screen.getByLabelText('Аккаунт'));
  // acc-2 has no rows on the page yet is still offered by the dropdown
  expect(screen.getByRole('button', { name: '+79995554433' })).toBeInTheDocument();
});

test('prepends a live SSE event to the newest page (key-scoped, no blanket invalidate)', async () => {
  routeLogs({ items: [logRow(1, 'first_event')], next_cursor: null });
  const view = renderWithClient(<LogsPage />);
  // Spy the client used by this render tree.
  await waitFor(() => {
    expect(screen.getByText('first_event')).toBeInTheDocument();
  });
  act(() => {
    lastEventSource()?.emit(logRow(2, 'live_event'));
  });
  await waitFor(() => {
    expect(screen.getByText('live_event')).toBeInTheDocument();
  });
  view.unmount();
});

test('the SSE callback only mutates the logs query key, never a global invalidate', async () => {
  routeLogs({ items: [logRow(1, 'first_event')], next_cursor: null });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  render(
    <QueryClientProvider client={client}>
      <LogsPage />
    </QueryClientProvider>,
  );
  await waitFor(() => {
    expect(screen.getByText('first_event')).toBeInTheDocument();
  });
  act(() => {
    lastEventSource()?.emit(logRow(2, 'live_event'));
  });
  await waitFor(() => {
    expect(screen.getByText('live_event')).toBeInTheDocument();
  });
  // Live-tail writes straight into the logs cache entry; it must NOT invalidate.
  expect(invalidate).not.toHaveBeenCalled();
});

test('shows the empty state', async () => {
  routeLogs({ items: [], next_cursor: null });
  renderWithClient(<LogsPage />);
  await waitFor(() => {
    expect(screen.getByText('Записей нет')).toBeInTheDocument();
  });
});

test('paginates forward and back', async () => {
  routeLogs(
    { items: [logRow(1, 'warming_started')], next_cursor: '50' },
    { items: [logRow(2, 'warming_stopped')], next_cursor: null },
  );
  renderWithClient(<LogsPage />);
  await waitFor(() => {
    expect(screen.getByText('Прогрев запущен')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Вперёд'));
  await waitFor(() => {
    expect(screen.getByText('Прогрев остановлен')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Назад'));
  await waitFor(() => {
    expect(screen.getByText('Прогрев запущен')).toBeInTheDocument();
  });
});

test('applies the status and account filters', async () => {
  routeLogs({ items: [logRow(1, 'warming_stopped', 'error')], next_cursor: null });
  renderWithClient(<LogsPage />);
  await waitFor(() => {
    expect(screen.getByText('Прогрев остановлен')).toBeInTheDocument();
  });
  await userEvent.click(screen.getAllByText('Ошибка')[0]!);
  await userEvent.click(screen.getByLabelText('Аккаунт'));
  await userEvent.click(screen.getByRole('button', { name: '+79990001122' }));
  await waitFor(() => {
    const filtered = vi.mocked(fetch).mock.calls.some(([input]) => {
      const url = new URL((input as Request).url);
      return (
        url.searchParams.get('status') === 'error' && url.searchParams.get('account_id') === 'acc-1'
      );
    });
    expect(filtered).toBe(true);
  });
});
