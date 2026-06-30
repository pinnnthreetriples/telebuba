import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { ProxyAddModal } from './ProxyAddModal';

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

test('renders the shared form; cancel and close call onClose', async () => {
  const onClose = vi.fn();
  renderWithClient(<ProxyAddModal onClose={onClose} />);
  expect(screen.getByText('Добавить прокси')).toBeInTheDocument();
  expect(screen.getByText('Хост')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Отмена'));
  await userEvent.click(screen.getByLabelText('Закрыть'));
  expect(onClose).toHaveBeenCalledTimes(2);
});

test('add creates a proxy and closes', async () => {
  const onClose = vi.fn();
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({
      id: 'p1',
      proxy_type: 'socks5',
      host: '1.2.3.4',
      port: 1080,
      has_password: false,
      status: 'unknown',
      created_at: 'now',
      updated_at: 'now',
      used: 0,
      capacity: 3,
      free: 3,
    }),
  );
  renderWithClient(<ProxyAddModal onClose={onClose} />);

  const textboxes = screen.getAllByRole('textbox');
  await userEvent.type(textboxes[0]!, '1.2.3.4'); // host
  await userEvent.type(textboxes[1]!, '1080'); // port
  await userEvent.click(screen.getByText('Добавить'));

  await waitFor(() => {
    expect(onClose).toHaveBeenCalled();
  });
  const posted = vi.mocked(fetch).mock.calls.some(([input]) => {
    const request = input as Request;
    return request.url.includes('/api/v1/proxies') && request.method === 'POST';
  });
  expect(posted).toBe(true);
});
