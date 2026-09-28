import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { ProxySection } from './ProxySection';

const ACCOUNT: AccountRead = {
  account_id: 'acc-1',
  status: 'alive',
  proxy_id: 'p1',
  created_at: 'now',
  updated_at: 'now',
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
  });
}

test('assignment and detachment cannot run together', async () => {
  const proxy = (id: string, host: string) => ({
    id,
    proxy_type: 'socks5',
    host,
    port: 1080,
    has_password: false,
    status: 'tcp_working',
    created_at: 'now',
    updated_at: 'now',
    used: 1,
    capacity: 3,
    free: 2,
  });
  let finishAssign!: (response: Response) => void;
  let finishUnassign!: (response: Response) => void;
  const assignment = new Promise<Response>((resolve) => {
    finishAssign = resolve;
  });
  const detachment = new Promise<Response>((resolve) => {
    finishUnassign = resolve;
  });
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/proxies' && request.method === 'GET') {
      return Promise.resolve(
        jsonResponse({ proxies: [proxy('p1', '1.1.1.1'), proxy('p2', '2.2.2.2')] }),
      );
    }
    if (pathname === '/api/v1/proxies/p2/assign') return assignment;
    if (pathname === '/api/v1/proxies/unassign') return detachment;
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ProxySection account={ACCOUNT} />
    </QueryClientProvider>,
  );

  await userEvent.click(screen.getByText('Прокси'));
  expect(screen.getByText(/Если адрес уже есть в пуле/)).toBeInTheDocument();
  await userEvent.click(screen.getByText('Из пула'));
  await userEvent.click(await screen.findByRole('option', { name: '2.2.2.2:1080' }));
  const detach = screen.getByRole('button', { name: 'Отвязать прокси' });
  await waitFor(() => {
    expect(detach).toBeDisabled();
  });
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(
        ([input]) => new URL((input as Request).url).pathname === '/api/v1/proxies/unassign',
      ),
  ).toBe(false);

  finishAssign(jsonResponse(proxy('p2', '2.2.2.2')));
  await waitFor(() => {
    expect(detach).toBeEnabled();
  });
  await userEvent.click(detach);
  await waitFor(() => {
    expect(screen.getByRole('combobox', { name: 'Прокси-пул' })).toBeDisabled();
  });
  finishUnassign(new Response(null, { status: 204 }));
  await waitFor(() => {
    expect(detach).toBeEnabled();
  });
});

test('manual assignment uses the atomic endpoint and pool mode keeps its assign route', async () => {
  const proxy = (over: Record<string, unknown> = {}) => ({
    id: 'newp',
    proxy_type: 'socks5',
    host: '1.2.3.4',
    port: 1080,
    has_password: false,
    status: 'tcp_working',
    created_at: 'now',
    updated_at: 'now',
    used: 0,
    capacity: 3,
    free: 3,
    ...over,
  });
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/proxies' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ proxies: [proxy({ id: 'pool-1', host: '9.9.9.9' })] }));
    }
    if (pathname === '/api/v1/proxies/assign-by-endpoint') {
      return Promise.resolve(jsonResponse(proxy()));
    }
    if (pathname.endsWith('/assign')) return Promise.resolve(jsonResponse(proxy()));
    if (pathname.endsWith('/check')) return Promise.resolve(jsonResponse(proxy()));
    return Promise.resolve(jsonResponse({}));
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ProxySection account={ACCOUNT} />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByText('Прокси'));
  await userEvent.type(screen.getByLabelText('Host'), '1.2.3.4');
  await userEvent.type(screen.getByLabelText('Порт'), '1080');
  await userEvent.type(screen.getByLabelText('Логин'), 'u');
  await userEvent.type(screen.getByLabelText('Пароль', { selector: 'input' }), 'p');
  await userEvent.click(screen.getByRole('combobox', { name: 'Тип' }));
  await userEvent.click(screen.getByRole('option', { name: 'HTTPS' }));
  await userEvent.click(screen.getByRole('button', { name: 'Добавить и назначить' }));
  await userEvent.click(await screen.findByText('Заменить'));

  await waitFor(() => {
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(
          ([input]) =>
            new URL((input as Request).url).pathname === '/api/v1/proxies/assign-by-endpoint',
        ),
    ).toBe(true);
  });
  const manual = vi
    .mocked(fetch)
    .mock.calls.find(
      ([input]) =>
        new URL((input as Request).url).pathname === '/api/v1/proxies/assign-by-endpoint',
    );
  expect(manual).toBeDefined();
  expect(await (manual?.[0] as Request).clone().json()).toMatchObject({
    account_id: 'acc-1',
    proxy_type: 'https',
    host: '1.2.3.4',
    port: 1080,
    username: 'u',
    password: 'p',
  });
  expect(
    vi.mocked(fetch).mock.calls.some(([input]) => {
      const request = input as Request;
      return new URL(request.url).pathname === '/api/v1/proxies' && request.method === 'POST';
    }),
  ).toBe(false);
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Добавить и назначить' })).toBeEnabled();
  });
  expect(screen.getByText('Прокси доступен')).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText('Host'), '5');
  expect(screen.queryByText('Прокси доступен')).toBeNull();

  await userEvent.click(screen.getByText('Из пула'));
  await userEvent.click(await screen.findByRole('option', { name: '9.9.9.9:1080' }));
  await waitFor(() => {
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([input]) => (input as Request).url.includes('/proxies/pool-1/assign')),
    ).toBe(true);
  });
});
