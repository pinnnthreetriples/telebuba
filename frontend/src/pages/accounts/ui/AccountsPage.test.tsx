import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { AccountsPage } from './AccountsPage';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function mockFetchJson(body: unknown, status = 200): void {
  vi.mocked(fetch).mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  );
}

test('renders the account list fetched via the generated client', async () => {
  mockFetchJson({
    items: [{ account_id: 'acc-1', status: 'new', created_at: 'now', updated_at: 'now' }],
    next_cursor: null,
  });

  renderWithClient(<AccountsPage />);

  expect(screen.getByText('Загрузка…')).toBeInTheDocument();
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
});

test('renders the empty state when there are no accounts', async () => {
  mockFetchJson({ items: [], next_cursor: null });

  renderWithClient(<AccountsPage />);

  await waitFor(() => {
    expect(screen.getByText('Аккаунтов нет')).toBeInTheDocument();
  });
});

test('renders an error message when the request fails', async () => {
  mockFetchJson({ detail: 'boom' }, 500);

  renderWithClient(<AccountsPage />);

  await waitFor(() => {
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });
});
