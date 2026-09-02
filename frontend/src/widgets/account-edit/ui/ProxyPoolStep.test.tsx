import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { ProxyPoolStep } from './ProxyPoolStep';

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

const POOL_PROXY = {
  id: 'pool-1',
  proxy_type: 'socks5',
  host: 'nl-1.proxyhub.net',
  port: 1080,
  has_password: false,
  status: 'tcp_working',
  country_code: 'nl',
  created_at: 'now',
  updated_at: 'now',
  used: 0,
  capacity: 3,
  free: 3,
};

const SECOND_PROXY = { ...POOL_PROXY, id: 'pool-2', host: 'de-1.proxyhub.net', free: 1 };

// Routes the pool GET and hands every assign to `assign`; the default accepts.
function routePool(
  proxies: (typeof POOL_PROXY)[],
  assign: (request: Request) => Promise<Response> = () => Promise.resolve(jsonResponse(POOL_PROXY)),
) {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/proxies' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ proxies }));
    }
    if (pathname.endsWith('/assign')) return assign(request);
    return Promise.resolve(jsonResponse({}));
  });
}

async function assignCalls(): Promise<{ proxy: string; account: string }[]> {
  const requests = vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => request.url.endsWith('/assign'));
  return Promise.all(
    requests.map(async (request) => {
      const body = (await request.clone().json()) as { account_id: string };
      const proxy = new URL(request.url).pathname.split('/').at(-2) ?? '';
      return { proxy, account: body.account_id };
    }),
  );
}

test('single account: clicking a proxy assigns it and advances', async () => {
  routePool([POOL_PROXY]);
  const onDone = vi.fn();
  const onImported = vi.fn();
  renderWithClient(
    <ProxyPoolStep accountIds={['a1']} onBack={vi.fn()} onDone={onDone} onImported={onImported} />,
  );
  await userEvent.click(await screen.findByText('nl-1.proxyhub.net:1080'));
  await waitFor(() => {
    expect(onDone).toHaveBeenCalledTimes(1);
  });
  expect(await assignCalls()).toEqual([{ proxy: 'pool-1', account: 'a1' }]);
  expect(onImported).toHaveBeenCalledTimes(1);
  expect(screen.queryByText(/Назначено/)).not.toBeInTheDocument();
});

test('three accounts on a proxy with two free slots: assigns exactly two, stays', async () => {
  routePool([{ ...POOL_PROXY, free: 2 }]);
  const onDone = vi.fn();
  renderWithClient(
    <ProxyPoolStep
      accountIds={['a1', 'a2', 'a3']}
      onBack={vi.fn()}
      onDone={onDone}
      onImported={vi.fn()}
    />,
  );
  expect(screen.getByText('Назначено: 0 из 3')).toBeInTheDocument();
  await userEvent.click(await screen.findByText('nl-1.proxyhub.net:1080'));
  expect(await screen.findByText('Назначено: 2 из 3')).toBeInTheDocument();
  expect(screen.getByText('Без прокси: 1')).toBeInTheDocument();
  expect(await assignCalls()).toEqual([
    { proxy: 'pool-1', account: 'a1' },
    { proxy: 'pool-1', account: 'a2' },
  ]);
  expect(onDone).not.toHaveBeenCalled();
  // Its slots are spent locally, so the proxy leaves the list before any refetch.
  expect(screen.queryByText('nl-1.proxyhub.net:1080')).not.toBeInTheDocument();
});

test('distribute walks the free proxies greedily in list order', async () => {
  routePool([{ ...POOL_PROXY, free: 2 }, SECOND_PROXY]);
  const onImported = vi.fn();
  renderWithClient(
    <ProxyPoolStep
      accountIds={['a1', 'a2', 'a3']}
      onBack={vi.fn()}
      onDone={vi.fn()}
      onImported={onImported}
    />,
  );
  await userEvent.click(await screen.findByText('Распределить автоматически'));
  expect(await screen.findByText('Назначено: 3 из 3')).toBeInTheDocument();
  expect(await assignCalls()).toEqual([
    { proxy: 'pool-1', account: 'a1' },
    { proxy: 'pool-1', account: 'a2' },
    { proxy: 'pool-2', account: 'a3' },
  ]);
  expect(onImported).toHaveBeenCalledTimes(1);
  expect(screen.queryByText(/Без прокси/)).not.toBeInTheDocument();
  expect(screen.queryByText('Распределить автоматически')).not.toBeInTheDocument();
});

test('a refused assign shows the alert and keeps that account without a proxy', async () => {
  routePool([POOL_PROXY], async (request) => {
    const body = (await request.clone().json()) as { account_id: string };
    return body.account_id === 'a2'
      ? jsonResponse({ error: { code: 'conflict', message: 'full' } }, 409)
      : jsonResponse(POOL_PROXY);
  });
  const onDone = vi.fn();
  renderWithClient(
    <ProxyPoolStep
      accountIds={['a1', 'a2', 'a3']}
      onBack={vi.fn()}
      onDone={onDone}
      onImported={vi.fn()}
    />,
  );
  await userEvent.click(await screen.findByText('nl-1.proxyhub.net:1080'));
  expect(await screen.findByRole('alert')).toHaveTextContent('Не все аккаунты получили прокси');
  expect(screen.getByText('Назначено: 2 из 3')).toBeInTheDocument();
  expect(screen.getByText('Без прокси: 1')).toBeInTheDocument();
  expect(onDone).not.toHaveBeenCalled();
  // The proxy still has one free slot (2 of 3 taken); a retry offers it to a2 only.
  await userEvent.click(screen.getByText('nl-1.proxyhub.net:1080'));
  await waitFor(async () => {
    expect((await assignCalls()).at(-1)).toEqual({ proxy: 'pool-1', account: 'a2' });
  });
});

test('single-account distribute button is absent; Back and Done call through', async () => {
  routePool([POOL_PROXY]);
  const onBack = vi.fn();
  const onDone = vi.fn();
  renderWithClient(
    <ProxyPoolStep accountIds={['a1']} onBack={onBack} onDone={onDone} onImported={vi.fn()} />,
  );
  await screen.findByText('nl-1.proxyhub.net:1080');
  expect(screen.queryByText('Распределить автоматически')).not.toBeInTheDocument();
  await userEvent.click(screen.getByText('Назад'));
  expect(onBack).toHaveBeenCalledTimes(1);
  await userEvent.click(screen.getByText('Готово'));
  expect(onDone).toHaveBeenCalledTimes(1);
});

test('an empty pool shows the empty state', async () => {
  routePool([{ ...POOL_PROXY, free: 0 }]);
  renderWithClient(
    <ProxyPoolStep
      accountIds={['a1', 'a2']}
      onBack={vi.fn()}
      onDone={vi.fn()}
      onImported={vi.fn()}
    />,
  );
  expect(await screen.findByText('В пуле нет свободных прокси')).toBeInTheDocument();
  expect(screen.queryByText('Распределить автоматически')).not.toBeInTheDocument();
});
