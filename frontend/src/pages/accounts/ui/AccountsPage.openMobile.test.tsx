import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { mutationErrorText } from '@/shared/lib/query-client';
import { toastError } from '@/shared/ui';

import { AccountsPage } from './AccountsPage';

// The page has no <Toaster/> of its own, so the queue is what we assert on.
vi.mock('@/shared/ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/ui')>()),
  toastError: vi.fn(),
}));

const ACCOUNT: AccountRead = {
  account_id: 'acc-1',
  status: 'alive',
  created_at: 'now',
  updated_at: 'now',
  proxy_id: 'p1', // mobile launcher is disabled without proxy
};

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function refusal(code: string): Response {
  return new Response(JSON.stringify({ error: { code: 'bad_request', message: code } }), {
    status: 400,
    headers: { 'Content-Type': 'application/json' },
  });
}

function routeApi(openMobile: () => Response, gate: Promise<void>) {
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
    if (pathname === '/api/v1/accounts/acc-1/open-mobile') return gate.then(openMobile);
    return Promise.resolve(json(ACCOUNT));
  });
}

function mobileButton(): HTMLElement {
  return screen.getAllByTitle('Мобильный Telegram (Эмулятор)')[0]!;
}

async function clickTheMobileButton(openMobile: () => Response) {
  vi.mocked(toastError).mockClear();
  let release = () => {};
  routeApi(
    openMobile,
    new Promise<void>((resolve) => {
      release = resolve;
    }),
  );
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
    mutationCache: new MutationCache({
      onError: (error) => toastError(mutationErrorText(error)),
    }),
  });
  render(
    <QueryClientProvider client={queryClient}>
      <AccountsPage />
    </QueryClientProvider>,
  );
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
  await userEvent.click(mobileButton());
  await waitFor(() => {
    expect(mobileButton()).toBeDisabled();
  });
  return { release, button: mobileButton };
}

test('openMobile success leaves the UI quiet without error toasts', async () => {
  const { release, button } = await clickTheMobileButton(() =>
    json({ launched: true, signed_in: true, instance_name: 'telebuba_acc-1' }),
  );
  release();
  await waitFor(() => {
    expect(button()).not.toBeDisabled();
  });
  expect(toastError).not.toHaveBeenCalled();
});

test('openMobile not signed-in toasts the failure message', async () => {
  const { release, button } = await clickTheMobileButton(() =>
    json({ launched: true, signed_in: false, instance_name: 'telebuba_acc-1' }),
  );
  release();
  await waitFor(() => {
    expect(button()).not.toBeDisabled();
  });
  expect(toastError).toHaveBeenCalledWith(
    'Не удалось войти в мобильное приложение автоматически',
  );
});

test('openMobile refusal toasts the error and re-enables button', async () => {
  const { release, button } = await clickTheMobileButton(() =>
    refusal('LDPlayer or ADB binary not located on host'),
  );
  release();
  await waitFor(() => {
    expect(button()).not.toBeDisabled();
  });
  expect(toastError).toHaveBeenCalledWith('LDPlayer or ADB binary not located on host');
});
