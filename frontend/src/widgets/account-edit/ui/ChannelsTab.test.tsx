import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { ChannelsTab } from './ChannelsTab';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const CHANNELS = {
  items: [
    { channel_id: '123', title: 'Мой канал', username: 'mychan', participants_count: 42 },
    { channel_id: '456', title: 'Второй', username: null, participants_count: null },
  ],
  next_cursor: null,
};

const DETAIL = {
  channel_id: '123',
  title: 'Мой канал',
  username: 'mychan',
  participants_count: 42,
  about: '',
};

function routeApi() {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/acc-1/channels' && request.method === 'GET') {
      return Promise.resolve(jsonResponse(CHANNELS));
    }
    if (pathname === '/api/v1/accounts/acc-1/channels/123' && request.method === 'GET') {
      return Promise.resolve(jsonResponse(DETAIL));
    }
    if (pathname === '/api/v1/accounts/acc-1/channels/123/posts' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
}

function calls(fragment: string, method = 'POST'): number {
  return vi.mocked(fetch).mock.calls.filter(([input]) => {
    const request = input as Request;
    return new URL(request.url).pathname.endsWith(fragment) && request.method === method;
  }).length;
}

test('renders the channel list with public/private badges and counts', async () => {
  routeApi();
  renderWithClient(<ChannelsTab accountId="acc-1" />);
  expect(await screen.findByText('Мой канал')).toBeInTheDocument();
  expect(screen.getByText('Публичный')).toBeInTheDocument();
  expect(screen.getByText('@mychan')).toBeInTheDocument();
  expect(screen.getByText('42 подписчиков')).toBeInTheDocument();
  expect(screen.getByText('Второй')).toBeInTheDocument();
  expect(screen.getByText('Приватный')).toBeInTheDocument();
});

test('shows the empty state when the account has no channels', async () => {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(jsonResponse({ items: [], next_cursor: null })),
  );
  renderWithClient(<ChannelsTab accountId="acc-1" />);
  expect(await screen.findByText('У аккаунта пока нет каналов')).toBeInTheDocument();
});

test('a failed list load shows the translated reason and retry recovers', async () => {
  let failing = true;
  vi.mocked(fetch).mockImplementation(() => {
    if (failing) {
      return Promise.resolve(
        jsonResponse({ error: { code: 'bad_request', message: 'channel_read_failed' } }, 400),
      );
    }
    return Promise.resolve(jsonResponse(CHANNELS));
  });
  renderWithClient(<ChannelsTab accountId="acc-1" />);
  expect(
    await screen.findByText('Не удалось получить данные канала из Telegram'),
  ).toBeInTheDocument();

  failing = false;
  await userEvent.click(screen.getByText('Повторить'));
  expect(await screen.findByText('Мой канал')).toBeInTheDocument();
});

test('deleting a channel asks for confirmation, fires the endpoint and refreshes', async () => {
  routeApi();
  renderWithClient(<ChannelsTab accountId="acc-1" />);
  await screen.findByText('Мой канал');

  await userEvent.click(screen.getAllByLabelText('Удалить канал')[0] as HTMLElement);
  expect(await screen.findByText('Удалить канал?')).toBeInTheDocument();
  await userEvent.click(screen.getByText('Удалить', { selector: 'button' }));

  await waitFor(() => {
    expect(calls('/channels/123/delete')).toBe(1);
  });
  // Settled → the list re-pulls (initial GET + the invalidated refetch).
  await waitFor(() => {
    expect(calls('/accounts/acc-1/channels', 'GET')).toBe(2);
  });
});

test('the edit button opens the channel editor with the live detail', async () => {
  routeApi();
  renderWithClient(<ChannelsTab accountId="acc-1" />);
  await screen.findByText('Мой канал');

  await userEvent.click(screen.getAllByText('Изменить')[0] as HTMLElement);
  // The editor's title field carries the fetched detail.
  expect(await screen.findByDisplayValue('Мой канал')).toBeInTheDocument();
  expect(await screen.findByText('Посты')).toBeInTheDocument();
});

test('the create button opens the create dialog', async () => {
  routeApi();
  renderWithClient(<ChannelsTab accountId="acc-1" />);
  await screen.findByText('Мой канал');
  await userEvent.click(screen.getByText('Создать канал'));
  expect(await screen.findByText('Новый канал')).toBeInTheDocument();
});
