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
  proxy_id: 'p1', // the globe is dead without one
};

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** The 400 envelope a service refusal arrives in; `message` is the translated code. */
function refusal(code: string): Response {
  return new Response(JSON.stringify({ error: { code: 'bad_request', message: code } }), {
    status: 400,
    headers: { 'Content-Type': 'application/json' },
  });
}

function routeApi(openWeb: () => Response, gate: Promise<void>) {
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
    // Held open until the test lets it answer, so "the click has finished" is an
    // observable state change rather than a guess about microtask ordering.
    if (pathname === '/api/v1/accounts/acc-1/open-web') return gate.then(openWeb);
    return Promise.resolve(json(ACCOUNT));
  });
}

function globe(): HTMLElement {
  // Re-queried every time: the table re-renders around the click, so a captured node
  // can be a detached one by the time the assertion runs.
  return screen.getAllByTitle('Открыть в Telegram Web')[0]!;
}

/** Click the globe; returns the button getter and the release for the response. */
async function clickTheGlobe(openWeb: () => Response) {
  vi.mocked(toastError).mockClear();
  let release = () => {};
  routeApi(
    openWeb,
    new Promise<void>((resolve) => {
      release = resolve;
    }),
  );
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
    // The same one line the app wires in `query-client.ts`: failures reach the operator
    // through this cache, not through the page. Without it a test cannot tell a page
    // that stays quiet on a refusal from one that toasts a second time over it.
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
  await userEvent.click(globe());
  // The globe is disabled for exactly as long as the request is in flight, so this
  // proves the click really started and gives the settle below something to wait on.
  await waitFor(() => {
    expect(globe()).toBeDisabled();
  });
  return { globe, release };
}

test('a window that opened without signing in tells the operator to finish the login', async () => {
  // 200 with signed_in false: the window is up but every QR token was refused (or the
  // 2FA screen was left standing). Ignoring the body showed this as a silent success.
  const { release } = await clickTheGlobe(() => json({ launched: true, signed_in: false }));
  release();

  await waitFor(() => {
    expect(toastError).toHaveBeenCalledWith(
      'Окно открыто, но аккаунт не вошёл — завершите вход в этом окне.',
    );
  });
});

test('a completed login says nothing', async () => {
  const { release } = await clickTheGlobe(() => json({ launched: true, signed_in: true }));
  release();

  // Wait for the whole chain to SETTLE — the busy flag is cleared in the `.finally`,
  // after the toast branch has had its chance. Waiting only for the request to be
  // issued left an inverted condition to be caught by microtask luck.
  await waitFor(() => {
    expect(globe()).toBeEnabled();
  });
  expect(toastError).not.toHaveBeenCalled();
});

test('a refused open re-enables the globe and adds no toast of its own', async () => {
  // Every service refusal (no proxy, relay or browser launch failure, shutting down)
  // arrives as a 400 carrying a code, and both other tests answer 200 — so nothing
  // pinned what the page does on the failure path. Two things must hold: the busy flag
  // is cleared in the `.finally`, which runs after the `.catch`, or the row's globe
  // stays dead until a reload; and the not-signed-in branch belongs to `.then` alone,
  // or a refusal toasts twice — the cache's real reason plus a wrong "finish the login".
  const { release } = await clickTheGlobe(() => refusal('web_login_browser_failed'));
  release();

  await waitFor(() => {
    expect(globe()).toBeEnabled();
  });
  expect(toastError).toHaveBeenCalledTimes(1);
  expect(toastError).toHaveBeenCalledWith(
    expect.stringContaining('Не удалось запустить окно браузера'),
  );
});
