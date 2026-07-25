import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountPrivacyView, BulkPrivacyResult, PrivacySettingsResult } from '@/shared/api';

import { PrivacyTab } from './PrivacyTab';

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

const MIXED = {
  profile_photo: 'contacts',
  bio: 'nobody',
  last_seen: 'everybody',
} satisfies PrivacySettingsResult;

const BULK = {
  outcomes: [
    { account_id: 'acc-1', status: 'ok', error: null },
    { account_id: 'acc-2', status: 'failed', error: 'account_frozen' },
    { account_id: 'acc-3', status: 'skipped', error: null },
  ],
  ok: 1,
  failed: 1,
  skipped: 1,
} satisfies BulkPrivacyResult;

// GET answers with `read`, the PUT with `written` (the backend re-reads and
// returns the live state), the fleet POST with BULK.
function routeApi(read: AccountPrivacyView, written: AccountPrivacyView = read) {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/privacy/all') return Promise.resolve(jsonResponse(BULK));
    if (request.method === 'PUT') return Promise.resolve(jsonResponse(written));
    if (pathname === '/api/v1/accounts/acc-1/privacy') return Promise.resolve(jsonResponse(read));
    return Promise.resolve(jsonResponse({}));
  });
}

function requests(method: string, fragment: string): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => request.method === method && request.url.includes(fragment));
}

async function lastBody(method: string, fragment: string): Promise<unknown> {
  const sent = requests(method, fragment);
  const last = sent[sent.length - 1];
  if (!last) throw new Error(`no ${method} to ${fragment}`);
  return JSON.parse(await last.clone().text());
}

function row(label: string): HTMLElement {
  return screen.getByRole('group', { name: label });
}

test('renders the three live levels from the query response', async () => {
  routeApi({ settings: MIXED, error: null });
  renderWithClient(<PrivacyTab accountId="acc-1" />);

  expect(await screen.findByText('Фото профиля')).toBeInTheDocument();
  expect(within(row('Фото профиля')).getByText('Сейчас: Контакты')).toBeInTheDocument();
  expect(within(row('Описание (bio)')).getByText('Сейчас: Никто')).toBeInTheDocument();
  expect(within(row('Был в сети')).getByText('Сейчас: Все')).toBeInTheDocument();

  // The pressed button per row is the live level, not a default.
  expect(within(row('Фото профиля')).getByRole('button', { pressed: true })).toHaveTextContent(
    'Контакты',
  );
  expect(within(row('Был в сети')).getByRole('button', { pressed: true })).toHaveTextContent('Все');
});

test('an unknown level renders as unknown, presses nothing and blocks the fleet apply', async () => {
  routeApi({
    settings: { profile_photo: 'unknown', bio: 'unknown', last_seen: 'unknown' },
    error: null,
  });
  renderWithClient(<PrivacyTab accountId="acc-1" />);

  expect(await screen.findByText('Фото профиля')).toBeInTheDocument();
  expect(within(row('Описание (bio)')).getByText('Сейчас: Неизвестно')).toBeInTheDocument();
  expect(
    within(row('Описание (bio)')).getByText(
      'Telegram вернул правило, которое дашборд не распознаёт — выберите уровень заново',
    ),
  ).toBeInTheDocument();
  // Nothing is preselected anywhere — an unrecognised rule must not read as "Все".
  expect(screen.queryAllByRole('button', { pressed: true })).toHaveLength(0);
  // Every key is unsendable, so an all-null 422 body cannot be fired.
  expect(screen.getByRole('button', { name: 'Применить ко всем аккаунтам' })).toBeDisabled();
});

test('picking a level sends only that key and adopts the re-read state', async () => {
  routeApi(
    { settings: MIXED, error: null },
    { settings: { ...MIXED, bio: 'contacts' }, error: null },
  );
  renderWithClient(<PrivacyTab accountId="acc-1" />);
  await screen.findByText('Описание (bio)');

  await userEvent.click(within(row('Описание (bio)')).getByRole('button', { name: 'Контакты' }));

  await waitFor(() => {
    expect(requests('PUT', '/accounts/acc-1/privacy')).toHaveLength(1);
  });
  expect(await lastBody('PUT', '/accounts/acc-1/privacy')).toEqual({ bio: 'contacts' });
  // The PUT response seeds the cache, so the row reflects the new level without
  // a second GET.
  expect(await within(row('Описание (bio)')).findByText('Сейчас: Контакты')).toBeInTheDocument();
  expect(requests('GET', '/accounts/acc-1/privacy')).toHaveLength(1);
});

test('«Открыть профиль всем» sends everybody for all three keys', async () => {
  const opened = { profile_photo: 'everybody', bio: 'everybody', last_seen: 'everybody' } as const;
  routeApi({ settings: MIXED, error: null }, { settings: opened, error: null });
  renderWithClient(<PrivacyTab accountId="acc-1" />);
  await screen.findByText('Фото профиля');

  await userEvent.click(screen.getByRole('button', { name: 'Открыть профиль всем' }));

  await waitFor(() => {
    expect(requests('PUT', '/accounts/acc-1/privacy')).toHaveLength(1);
  });
  expect(await lastBody('PUT', '/accounts/acc-1/privacy')).toEqual(opened);
});

test('the fleet-wide apply only fires after the confirmation step', async () => {
  routeApi({ settings: MIXED, error: null });
  renderWithClient(<PrivacyTab accountId="acc-1" />);
  await screen.findByText('Фото профиля');

  await userEvent.click(screen.getByRole('button', { name: 'Применить ко всем аккаунтам' }));
  expect(await screen.findByText('Применить ко всем аккаунтам?')).toBeInTheDocument();
  // The dialog names the levels it is about to push fleet-wide: a generic
  // prompt would let one click propagate a restricted profile to every account.
  expect(
    screen.getByText(/Фото профиля — Контакты · Описание \(bio\) — Никто · Был в сети — Все/),
  ).toBeInTheDocument();
  expect(requests('POST', '/accounts/privacy/all')).toHaveLength(0);

  await userEvent.click(screen.getByRole('button', { name: 'Применить' }));
  await waitFor(() => {
    expect(requests('POST', '/accounts/privacy/all')).toHaveLength(1);
  });
  // The levels shown on the tab are what the fleet gets.
  expect(await lastBody('POST', '/accounts/privacy/all')).toEqual(MIXED);
});

test('the fleet result shows the counts and the failing accounts', async () => {
  routeApi({ settings: MIXED, error: null });
  renderWithClient(<PrivacyTab accountId="acc-1" />);
  await screen.findByText('Фото профиля');

  await userEvent.click(screen.getByRole('button', { name: 'Применить ко всем аккаунтам' }));
  await userEvent.click(await screen.findByRole('button', { name: 'Применить' }));

  expect(await screen.findByText('Применено: 1')).toBeInTheDocument();
  expect(screen.getByText('Ошибок: 1')).toBeInTheDocument();
  expect(screen.getByText('Пропущено: 1')).toBeInTheDocument();
  // A failure is inspectable, not swallowed into the count.
  expect(screen.getByText('acc-2 — account_frozen')).toBeInTheDocument();
});

test('a refused read renders the reason instead of an empty form', async () => {
  routeApi({ settings: null, error: 'flood_wait' });
  renderWithClient(<PrivacyTab accountId="acc-1" />);

  expect(
    await screen.findByText('Не удалось прочитать настройки приватности из Telegram (flood_wait)'),
  ).toBeInTheDocument();
  expect(screen.queryByText('Фото профиля')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Открыть профиль всем' })).not.toBeInTheDocument();
});

test('a rejected read shows the envelope code and retry recovers', async () => {
  let failing = true;
  vi.mocked(fetch).mockImplementation(() => {
    if (failing) {
      return Promise.resolve(
        jsonResponse({ error: { code: 'bad_request', message: 'privacy_read_failed' } }, 400),
      );
    }
    return Promise.resolve(jsonResponse({ settings: MIXED, error: null }));
  });
  renderWithClient(<PrivacyTab accountId="acc-1" />);
  expect(
    await screen.findByText(
      'Не удалось прочитать настройки приватности из Telegram (privacy_read_failed)',
    ),
  ).toBeInTheDocument();

  failing = false;
  await userEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  expect(await screen.findByText('Фото профиля')).toBeInTheDocument();
});
