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

test('delete fires a DELETE request', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (request.method === 'DELETE') {
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    return Promise.resolve(jsonResponse({ proxies: [proxy()] }));
  });
  renderWithClient(<ProxyPool onAdd={vi.fn()} />);
  await waitFor(() => {
    expect(screen.getByText('nl.example:1080')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByLabelText('Удалить'));
  await waitFor(() => {
    const sawDelete = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).method === 'DELETE');
    expect(sawDelete).toBe(true);
  });
});
