import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { ProxyPool } from './ProxyPool';

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

interface ProxyOverrides {
  used?: number;
  free?: number;
  status?: 'unknown' | 'tcp_working' | 'failed';
  country_code?: string | null;
  last_error?: string | null;
}

function proxy(over: ProxyOverrides = {}) {
  return {
    id: 'p1',
    proxy_type: 'socks5',
    host: 'nl.example',
    port: 1080,
    has_password: false,
    status: 'tcp_working',
    country_code: 'NL',
    used: 2,
    capacity: 3,
    free: 1,
    created_at: 'now',
    updated_at: 'now',
    ...over,
  };
}

test('renders pool cards with usage', async () => {
  vi.mocked(fetch).mockResolvedValue(jsonResponse({ proxies: [proxy()] }));
  renderWithClient(<ProxyPool onAdd={vi.fn()} />);
  await waitFor(() => {
    expect(screen.getByText('nl.example:1080')).toBeInTheDocument();
  });
  expect(screen.getByText('2 / 3')).toBeInTheDocument();
  // The working status resolves to its label, not the raw i18n key (the key must
  // match the ProxyStatus value `tcp_working`, not `working`).
  expect(screen.getByText('Работает')).toBeInTheDocument();
  expect(screen.queryByText(/proxyPool\.status/)).not.toBeInTheDocument();
});

test('warns clearly when a proxy check failed (flag gone, no silent card)', async () => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({
      proxies: [proxy({ status: 'failed', country_code: null, last_error: 'connect timeout' })],
    }),
  );
  renderWithClient(<ProxyPool onAdd={vi.fn()} />);
  await waitFor(() => {
    expect(screen.getByText('nl.example:1080')).toBeInTheDocument();
  });
  // The dead proxy is called out in words, and the raw reason is on hover.
  expect(screen.getByText('Не работает')).toBeInTheDocument();
  expect(screen.getByTitle('connect timeout')).toBeInTheDocument();
});

test('shows the empty state and triggers add', async () => {
  const onAdd = vi.fn();
  vi.mocked(fetch).mockResolvedValue(jsonResponse({ proxies: [] }));
  renderWithClient(<ProxyPool onAdd={onAdd} />);
  await waitFor(() => {
    expect(screen.getByText('Пул пуст')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Добавить прокси'));
  expect(onAdd).toHaveBeenCalled();
});

function routeWithDelete() {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (request.method === 'DELETE') {
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    return Promise.resolve(jsonResponse({ proxies: [proxy()] }));
  });
}

function sawDelete(): boolean {
  return vi.mocked(fetch).mock.calls.some(([input]) => (input as Request).method === 'DELETE');
}

test('delete asks for confirmation, then fires the DELETE on confirm', async () => {
  routeWithDelete();
  renderWithClient(<ProxyPool onAdd={vi.fn()} />);
  await waitFor(() => {
    expect(screen.getByText('nl.example:1080')).toBeInTheDocument();
  });

  // the card × opens a confirm dialog — nothing is deleted yet
  await userEvent.click(screen.getByLabelText('Удалить'));
  expect(screen.getByText(/Удалить прокси/)).toBeInTheDocument();
  expect(sawDelete()).toBe(false);

  // confirming fires the DELETE
  await userEvent.click(screen.getByText('Удалить'));
  await waitFor(() => {
    expect(sawDelete()).toBe(true);
  });
});

test('cancelling the confirm dialog does not delete', async () => {
  routeWithDelete();
  renderWithClient(<ProxyPool onAdd={vi.fn()} />);
  await waitFor(() => {
    expect(screen.getByText('nl.example:1080')).toBeInTheDocument();
  });

  await userEvent.click(screen.getByLabelText('Удалить'));
  await userEvent.click(screen.getByText('Отмена'));
  expect(sawDelete()).toBe(false);
});
