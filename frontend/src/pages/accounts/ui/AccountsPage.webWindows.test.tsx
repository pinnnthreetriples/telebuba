import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { toastError, toastSuccess } from '@/shared/ui';

import { AccountsPage } from './AccountsPage';

// Every globe click keeps a full Chrome process per account, so the page shows how
// many are up, offers to close them all, and warns once there are many.

// The page has no <Toaster/> of its own, so the queue is what we assert on.
vi.mock('@/shared/ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/ui')>()),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
}));

const ACCOUNT: AccountRead = {
  account_id: 'acc-1',
  status: 'alive',
  created_at: 'now',
  updated_at: 'now',
  proxy_id: 'p1', // the globe is dead without one
};

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** Route the API around a mutable open-window count; returns how to change it. */
function routeApi(initialCount: number) {
  const state = { count: initialCount, closes: 0 };
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/stats') {
      return Promise.resolve(json({ total: 1, active: 1, idle: 0, needs_code: 0, problem: 0 }));
    }
    if (pathname === '/api/v1/accounts') {
      return Promise.resolve(json({ items: [ACCOUNT], next_cursor: null }));
    }
    if (pathname === '/api/v1/proxies') return Promise.resolve(json({ proxies: [] }));
    if (pathname === '/api/v1/accounts/web-windows') {
      return Promise.resolve(json({ count: state.count }));
    }
    if (pathname === '/api/v1/accounts/web-windows/close' && request.method === 'POST') {
      const closed = state.count;
      state.count = 0;
      state.closes += 1;
      return Promise.resolve(json({ count: closed }));
    }
    if (pathname === '/api/v1/accounts/acc-1/open-web') {
      state.count += 1;
      return Promise.resolve(json({ launched: true, signed_in: true }));
    }
    return Promise.resolve(json(ACCOUNT));
  });
  return state;
}

async function renderPage() {
  vi.mocked(toastError).mockClear();
  vi.mocked(toastSuccess).mockClear();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AccountsPage />
    </QueryClientProvider>,
  );
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
}

function globe(): HTMLElement {
  return screen.getAllByTitle('Открыть в Telegram Web')[0]!;
}

async function openOneMore() {
  await userEvent.click(globe());
  await waitFor(() => {
    expect(globe()).toBeEnabled();
  });
}

test('no close-all action while no window is open', async () => {
  routeApi(0);
  await renderPage();

  expect(screen.queryByTitle(/Закрыть все окна Telegram Web/)).not.toBeInTheDocument();
});

test('close-all names the open count, closes them and reports how many', async () => {
  const state = routeApi(3);
  await renderPage();

  const closeAll = await screen.findByTitle('Закрыть все окна Telegram Web (открыто: 3)');
  await userEvent.click(closeAll);

  await waitFor(() => {
    expect(toastSuccess).toHaveBeenCalledWith('Закрыто окон Telegram Web: 3');
  });
  expect(state.closes).toBe(1);
  // Nothing is open any more, so the action goes away until the next globe click.
  await waitFor(() => {
    expect(screen.queryByTitle(/Закрыть все окна Telegram Web/)).not.toBeInTheDocument();
  });
});

test('a globe click shows the action with the fresh count', async () => {
  routeApi(0);
  await renderPage();

  await openOneMore();

  expect(
    await screen.findByTitle('Закрыть все окна Telegram Web (открыто: 1)'),
  ).toBeInTheDocument();
});

test('the open that reaches eight windows warns; seven does not', async () => {
  routeApi(6);
  await renderPage();

  await openOneMore();
  await screen.findByTitle('Закрыть все окна Telegram Web (открыто: 7)');
  expect(toastError).not.toHaveBeenCalled();

  await openOneMore();

  await waitFor(() => {
    expect(toastError).toHaveBeenCalledWith(
      'Открыто окон Telegram Web: 8. Каждое — отдельный Chrome, закройте те, что больше не нужны.',
    );
  });
});

test('the warning fires once per crossing, not on every open past it', async () => {
  routeApi(7);
  await renderPage();

  await openOneMore();
  await waitFor(() => {
    expect(toastError).toHaveBeenCalledTimes(1);
  });
  await openOneMore();
  await screen.findByTitle('Закрыть все окна Telegram Web (открыто: 9)');
  expect(toastError).toHaveBeenCalledTimes(1);

  // Back under the threshold re-arms it.
  await userEvent.click(screen.getByTitle('Закрыть все окна Telegram Web (открыто: 9)'));
  await waitFor(() => {
    expect(screen.queryByTitle(/Закрыть все окна Telegram Web/)).not.toBeInTheDocument();
  });
  for (let i = 0; i < 8; i += 1) await openOneMore();
  await waitFor(() => {
    expect(toastError).toHaveBeenCalledTimes(2);
  });
});
