import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type {
  AccountRead,
  AccountSession,
  AccountSessionAudit,
  SessionCloseResult,
} from '@/shared/api';

import { SessionAuditSection } from './SessionAuditSection';

const ACCOUNT: AccountRead = {
  account_id: 'acc-1',
  status: 'alive',
  phone: '+79051184490',
  created_at: 'now',
  updated_at: 'now',
  devices: ['desktop'],
  proxy_id: 'proxy-1',
};
const URL_PATH = '/api/v1/accounts/acc-1/sessions';

function session(overrides: Partial<AccountSession> = {}): AccountSession {
  return {
    hash: '1',
    is_current: false,
    is_ours: false,
    official_app: false,
    device_model: 'Pixel 8',
    platform: 'Android',
    system_version: 'Android 14',
    app_name: 'Telegram Android',
    ip: '192.0.2.10',
    country: 'Nowhere',
    date_created: '2026-10-31T08:30:00+00:00',
    date_active: '2026-11-01T08:30:00+00:00',
    unconfirmed: false,
    ...overrides,
  };
}

function audit(sessions: AccountSession[], checkedAt: string | null): AccountSessionAudit {
  return {
    account_id: 'acc-1',
    checked_at: checkedAt,
    ttl_days: 180,
    total: sessions.length,
    foreign_count: sessions.filter((s) => !s.is_ours).length,
    sessions,
  };
}

const OURS = session({
  hash: '9',
  is_current: true,
  is_ours: true,
  device_model: 'Desktop PC',
  app_name: 'Telegram Desktop',
  platform: 'Windows',
  system_version: '10',
  ip: '198.51.100.7',
});
const STRANGER = session({ hash: '2', unconfirmed: true });

// GET answers `current`; the POST that re-checks answers `next`.
function serve(current: AccountSessionAudit, next: AccountSessionAudit = current): void {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const body = request.method === 'GET' ? current : next;
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  });
}

function posts(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => new URL(request.url).pathname.startsWith(URL_PATH))
    .filter((request) => request.method === 'POST');
}

async function renderSection(account: AccountRead = ACCOUNT) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <SessionAuditSection account={account} />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByText('Сессии'));
}

test('foreign sessions raise a warning and are listed apart from ours', async () => {
  serve(audit([OURS, STRANGER], '2026-11-02T10:00:00+00:00'));
  await renderSection();

  expect(await screen.findByRole('alert')).toHaveTextContent('Сессий, которые не наши: 1');
  expect(screen.getByText('Чужие сессии')).toBeInTheDocument();
  expect(screen.getByText('Наши сессии')).toBeInTheDocument();
  expect(screen.getByText('Pixel 8 · Telegram Android')).toBeInTheDocument();
  expect(screen.getByText('192.0.2.10 · Nowhere')).toBeInTheDocument();
  expect(screen.getByText('не подтверждена')).toBeInTheDocument();
  expect(screen.getByText('это соединение')).toBeInTheDocument();
  expect(screen.getAllByText('Чужих: 1')).not.toHaveLength(0);
});

test('an account with only its own sessions says so and raises no warning', async () => {
  serve(audit([OURS], '2026-11-02T10:00:00+00:00'));
  await renderSection();

  expect(await screen.findByText('Чужих сессий нет.')).toBeInTheDocument();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.getByText('Только наши')).toBeInTheDocument();
});

test('before the first audit the card says so', async () => {
  serve(audit([], null));
  await renderSection();

  expect(await screen.findByText('Ещё не проверяли')).toBeInTheDocument();
});

test('re-checking reads Telegram again and shows the new answer', async () => {
  serve(
    audit([OURS], '2026-11-02T10:00:00+00:00'),
    audit([OURS, STRANGER], '2026-11-03T10:00:00+00:00'),
  );
  await renderSection();

  await userEvent.click(await screen.findByRole('button', { name: 'Проверить сессии' }));

  expect(await screen.findByRole('alert')).toHaveTextContent('Сессий, которые не наши: 1');
  expect(posts().map((request) => new URL(request.url).pathname)).toEqual([`${URL_PATH}/audit`]);
});

test('an account without a proxy cannot be checked', async () => {
  serve(audit([], null));
  await renderSection({ ...ACCOUNT, proxy_id: null });

  expect(await screen.findByText(/Сначала назначьте аккаунту прокси/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Проверить сессии' })).toBeDisabled();
});

test("the header carries the list's foreign count before the card is opened", () => {
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <SessionAuditSection account={{ ...ACCOUNT, foreign_sessions: 3 }} />
    </QueryClientProvider>,
  );

  expect(screen.getByText('Чужих: 3')).toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});

// The close action: GET answers the audit, the close POST answers `closed`.
function serveClose(current: SessionAuditAnswer, closed: SessionCloseResult): void {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const closing = new URL(request.url).pathname.endsWith('/sessions/close');
    return Promise.resolve(
      new Response(JSON.stringify(closing ? closed : current), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  });
}

type SessionAuditAnswer = AccountSessionAudit;

test('closing lists exactly the foreign sessions, sends only their hashes and tells the result', async () => {
  const after = audit([OURS], '2026-11-03T10:00:00+00:00');
  serveClose(audit([OURS, STRANGER], '2026-11-02T10:00:00+00:00'), {
    account_id: 'acc-1',
    outcomes: [{ hash: STRANGER.hash, status: 'closed' }],
    closed_count: 1,
    audit: after,
  });
  await renderSection();

  await userEvent.click(await screen.findByRole('button', { name: 'Закрыть чужие сессии' }));
  // The dialog names the foreign one, not ours.
  expect(await screen.findByText('Закрыть чужие сессии?')).toBeInTheDocument();
  expect(screen.getByText('Pixel 8 · Telegram Android · 192.0.2.10 · Nowhere')).toBeInTheDocument();
  expect(screen.queryByText(/Desktop PC · Telegram Desktop · /)).toBeNull();
  expect(posts()).toHaveLength(0);

  const confirm = screen.getAllByRole('button', { name: 'Закрыть чужие сессии' });
  await userEvent.click(confirm[confirm.length - 1] as HTMLElement);

  expect(await screen.findByText('Закрыто: 1')).toBeInTheDocument();
  const [request] = posts();
  expect(new URL(request?.url ?? '').pathname).toBe(`${URL_PATH}/close`);
  expect(await request?.clone().json()).toEqual({ hashes: [STRANGER.hash] });
  // The card shows what Telegram lists now.
  expect(await screen.findByText('Чужих сессий нет.')).toBeInTheDocument();
});

test('a session Telegram refused to close is told with the time left', async () => {
  serveClose(audit([OURS, STRANGER], '2026-11-02T10:00:00+00:00'), {
    account_id: 'acc-1',
    outcomes: [{ hash: STRANGER.hash, status: 'too_new', retry_after_seconds: 7200 }],
    closed_count: 0,
    audit: audit([OURS, STRANGER], '2026-11-03T10:00:00+00:00'),
  });
  await renderSection();

  await userEvent.click(await screen.findByRole('button', { name: 'Закрыть чужие сессии' }));
  const confirm = screen.getAllByRole('button', { name: 'Закрыть чужие сессии' });
  await userEvent.click(confirm[confirm.length - 1] as HTMLElement);

  expect(await screen.findByText('Закрыто: 0')).toBeInTheDocument();
  expect(screen.getByText(/повторите примерно через 2 ч/)).toBeInTheDocument();
});

test('with no foreign session there is nothing to close', async () => {
  serve(audit([OURS], '2026-11-02T10:00:00+00:00'));
  await renderSection();

  await screen.findByText('Чужих сессий нет.');
  expect(screen.queryByRole('button', { name: 'Закрыть чужие сессии' })).toBeNull();
});

// The sessions GET answers the audit, the re-key GET answers `rekey`.
function serveWithRekey(sessions: AccountSessionAudit, rekey: { rekeyed_at?: string }): void {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const body = new URL(request.url).pathname.endsWith('/rekey')
      ? { account_id: 'acc-1', ...rekey }
      : sessions;
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  });
}

test('a foreign session is red and badged «Чужая», ours is green and badged «Наша»', async () => {
  serve(audit([OURS, STRANGER], '2026-11-02T10:00:00+00:00'));
  await renderSection();

  const foreign = (await screen.findByText('Pixel 8 · Telegram Android')).closest('li');
  const ours = screen.getByText('Desktop PC · Telegram Desktop').closest('li');
  expect(foreign).toHaveClass('bg-danger-tint');
  expect(foreign).toHaveTextContent('Чужая');
  expect(ours).toHaveClass('bg-success-tint');
  expect(ours).toHaveTextContent('Наша');
  expect(screen.getByText('Не наш вход: аккаунтом владеет ещё кто-то.')).toBeInTheDocument();
  expect(screen.getByText('Входы, которыми пользуется Телебуба.')).toBeInTheDocument();
});

test('an account never re-keyed warns that its sign-in may be the seller’s', async () => {
  serveWithRekey(audit([OURS], '2026-11-02T10:00:00+00:00'), {});
  await renderSection();

  expect(await screen.findByText(/У купленного аккаунта его мог создать продавец/)).toBeVisible();
});

test('after a re-key the seller warning is gone', async () => {
  serveWithRekey(audit([OURS], '2026-11-02T10:00:00+00:00'), {
    rekeyed_at: '2026-11-02T10:00:00+00:00',
  });
  await renderSection();

  await screen.findByText('Чужих сессий нет.');
  expect(screen.queryByText(/У купленного аккаунта его мог создать продавец/)).toBeNull();
});
