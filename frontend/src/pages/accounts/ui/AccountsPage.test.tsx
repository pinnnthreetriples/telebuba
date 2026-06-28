import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { AccountsPage } from './AccountsPage';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function account(id: string): AccountRead {
  return { account_id: id, status: 'alive', created_at: 'now', updated_at: 'now' };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

// Route the mocked fetch by path/method so list + actions + pagination resolve.
function routeApi(options: { page1: unknown; page2?: unknown; listStatus?: number }) {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/accounts' && request.method === 'GET') {
      if (options.listStatus && options.listStatus >= 400) {
        return Promise.resolve(jsonResponse({ detail: 'boom' }, options.listStatus));
      }
      const body = url.searchParams.get('cursor')
        ? (options.page2 ?? options.page1)
        : options.page1;
      return Promise.resolve(jsonResponse(body));
    }
    return Promise.resolve(jsonResponse(account('acc-1')));
  });
}

test('shows the loading state first, then the table with live data', async () => {
  routeApi({ page1: { items: [account('acc-1')], next_cursor: null } });
  renderWithClient(<AccountsPage />);
  expect(screen.getByText('Загрузка…')).toBeInTheDocument();
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
});

test('shows the empty state', async () => {
  routeApi({ page1: { items: [], next_cursor: null } });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('Аккаунтов нет')).toBeInTheDocument();
  });
});

test('shows the error state', async () => {
  routeApi({ page1: {}, listStatus: 500 });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });
});

test('paginates forward with the next cursor', async () => {
  routeApi({
    page1: { items: [account('acc-1')], next_cursor: '20' },
    page2: { items: [account('acc-2')], next_cursor: null },
  });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Вперёд'));
  await waitFor(() => {
    expect(screen.getByText('acc-2')).toBeInTheDocument();
  });
});

test('runs the check action on a row', async () => {
  routeApi({ page1: { items: [account('acc-1')], next_cursor: null } });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByTitle('Проверить'));
  await waitFor(() => {
    const checked = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/accounts/check'));
    expect(checked).toBe(true);
  });
});
