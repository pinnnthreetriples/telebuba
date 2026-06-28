import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { NeurocommentPage } from './NeurocommentPage';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

const CAMPAIGN = {
  campaign_id: 'c1',
  name: 'Promo',
  prompt: 'mention the product',
  status: 'active',
  created_at: 'now',
  updated_at: 'now',
};

const BOARD = {
  campaign_id: 'c1',
  campaign_name: 'Promo',
  status: 'active',
  channels: [{ channel: '@news', status: 'ready', ready_accounts: 1, total_accounts: 1 }],
  accounts: [],
};

function routeApi() {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: null }),
      );
    }
    if (url.pathname === '/api/v1/accounts') {
      return Promise.resolve(
        jsonResponse({
          items: [{ account_id: 'acc-1', status: 'alive', created_at: 'n', updated_at: 'n' }],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });
}

test('renders campaigns and the board for the selected campaign', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getByText('@news')).toBeInTheDocument();
  });
  expect(screen.getByText('Готов')).toBeInTheDocument();
});

test('creates a campaign', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getByText('@news')).toBeInTheDocument();
  });
  await userEvent.type(screen.getByLabelText('Название'), 'New');
  await userEvent.type(screen.getByLabelText(/Промпт/), 'sell it');
  await userEvent.click(screen.getByText('Создать'));
  await waitFor(() => {
    const created = vi
      .mocked(fetch)
      .mock.calls.some(
        ([input]) =>
          (input as Request).url.endsWith('/neurocomment/campaigns') &&
          (input as Request).method === 'POST',
      );
    expect(created).toBe(true);
  });
});

test('starts the runtime with a listener account', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getByText('@news')).toBeInTheDocument();
  });
  await userEvent.selectOptions(screen.getByLabelText('Аккаунт-слушатель'), 'acc-1');
  await userEvent.click(screen.getByText('Запустить'));
  await waitFor(() => {
    const started = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.endsWith('/neurocomment/start'));
    expect(started).toBe(true);
  });
});
