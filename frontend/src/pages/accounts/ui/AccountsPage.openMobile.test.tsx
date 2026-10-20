import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { mutationErrorText } from '@/shared/lib/query-client';
import { toastError } from '@/shared/ui';

import { AccountsPage } from './AccountsPage';

// The phone button opens a mobile account in its local Android container, and a
// dialog that shows the start as it goes and how it ended.

vi.mock('@/shared/ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/ui')>()),
  toastError: vi.fn(),
}));

const MOBILE: AccountRead = {
  account_id: 'acc-1',
  status: 'alive',
  created_at: 'now',
  updated_at: 'now',
  proxy_id: 'p1',
  devices: ['desktop', 'mobile'],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function routeApi(options: {
  account?: AccountRead;
  enabled?: boolean;
  openMobile?: () => Response;
  gate?: Promise<void>;
  stage?: string;
}) {
  const account = options.account ?? MOBILE;
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/stats') {
      return Promise.resolve(json({ total: 1, active: 1, idle: 0, needs_code: 0, problem: 0 }));
    }
    if (pathname === '/api/v1/accounts') {
      return Promise.resolve(json({ items: [account], next_cursor: null }));
    }
    if (pathname === '/api/v1/accounts/emulator') {
      return Promise.resolve(json({ enabled: options.enabled ?? true }));
    }
    if (pathname === '/api/v1/proxies') return Promise.resolve(json({ proxies: [] }));
    if (pathname === '/api/v1/accounts/acc-1/open-mobile/progress') {
      return Promise.resolve(
        json({ account_id: 'acc-1', phone_id: 'telebuba-acc-1', stage: options.stage ?? 'pod' }),
      );
    }
    if (pathname === '/api/v1/accounts/acc-1/open-mobile') {
      const answer = options.openMobile ?? (() => json({}));
      return (options.gate ?? Promise.resolve()).then(answer);
    }
    return Promise.resolve(json(account));
  });
}

async function renderPage() {
  vi.mocked(toastError).mockClear();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
    // As the app's own cache: a mutation that shows its failure in place raises no toast.
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        if (!mutation.meta?.inlineError) toastError(mutationErrorText(error));
      },
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
}

function phone(): HTMLElement {
  return screen.getAllByRole('button', { name: 'Открыть на телефоне' })[0]!;
}

function openMobileCalls(): number {
  return vi
    .mocked(fetch)
    .mock.calls.filter(
      ([input]) =>
        new URL((input as Request).url).pathname === '/api/v1/accounts/acc-1/open-mobile',
    ).length;
}

test('openMobile starts the container and spins until it answers', async () => {
  let release = () => {};
  routeApi({
    openMobile: () => json({ account_id: 'acc-1', status: 'started', signed_in: true }),
    gate: new Promise<void>((resolve) => {
      release = resolve;
    }),
  });
  await renderPage();
  await waitFor(() => {
    expect(phone()).toBeEnabled();
  });

  await userEvent.click(phone());
  await waitFor(() => {
    expect(phone()).toBeDisabled();
  });
  release();

  await waitFor(() => {
    expect(phone()).toBeEnabled();
  });
  expect(openMobileCalls()).toBe(1);
  expect(toastError).not.toHaveBeenCalled();
});

test('the dialog shows the stage the start is in while it runs', async () => {
  routeApi({
    openMobile: () => json({ account_id: 'acc-1', status: 'started', signed_in: true }),
    gate: new Promise<void>(() => {}), // never answers: the start is still running
    stage: 'boot',
  });
  await renderPage();
  await waitFor(() => {
    expect(phone()).toBeEnabled();
  });

  await userEvent.click(phone());

  const dialog = await screen.findByRole('dialog');
  await waitFor(() => {
    expect(within(dialog).getByText('Загружается Android…')).toBeInTheDocument();
  });
  expect(within(dialog).getByText('Android загружается…')).toBeInTheDocument();
});

test('a sign-in that stopped short says why and to finish it on the screen', async () => {
  routeApi({
    openMobile: () =>
      json({
        account_id: 'acc-1',
        status: 'started',
        signed_in: false,
        sign_in_refusal: 'device_primary_unavailable',
      }),
  });
  await renderPage();
  await waitFor(() => {
    expect(phone()).toBeEnabled();
  });

  await userEvent.click(phone());

  const dialog = await screen.findByRole('dialog');
  await waitFor(() => {
    expect(
      within(dialog).getByText(
        'Телефон открыт, но вход не завершён. Закончите его вручную на экране телефона.',
      ),
    ).toBeInTheDocument();
  });
  expect(within(dialog).getByText(/Основная сессия недоступна/)).toBeInTheDocument();
  expect(toastError).not.toHaveBeenCalled();
});

test('a refusal is the translated code in the dialog, not a toast', async () => {
  routeApi({
    openMobile: () =>
      json({ error: { code: 'bad_request', message: 'emulator_proxy_unsupported' } }, 400),
  });
  await renderPage();
  await waitFor(() => {
    expect(phone()).toBeEnabled();
  });

  await userEvent.click(phone());

  const dialog = await screen.findByRole('dialog');
  await waitFor(() => {
    expect(within(dialog).getByText('Телефон не запустился.')).toBeInTheDocument();
  });
  expect(within(dialog).getByText(/только через SOCKS5/)).toBeInTheDocument();
  expect(toastError).not.toHaveBeenCalled();
});

test('the button is disabled, with the reason, while the emulator is switched off', async () => {
  routeApi({ enabled: false });
  await renderPage();

  expect(phone()).toBeDisabled();
  // The disabled button takes no pointer events: the hover reason sits on its wrapper,
  // and a screen reader hears it as the button's description.
  expect(phone().parentElement).toHaveAttribute('title', 'Эмулятор выключен (EMULATOR__ENABLED)');
  expect(phone()).toHaveAccessibleDescription('Эмулятор выключен (EMULATOR__ENABLED)');
});

test('an account without a mobile device cannot be opened on a phone', async () => {
  routeApi({ account: { ...MOBILE, devices: ['desktop'] } });
  await renderPage();
  await waitFor(() => {
    expect(phone()).toHaveAccessibleDescription('У аккаунта нет мобильного устройства');
  });
  expect(phone().parentElement).toHaveAttribute('title', 'У аккаунта нет мобильного устройства');

  expect(phone()).toBeDisabled();
});
