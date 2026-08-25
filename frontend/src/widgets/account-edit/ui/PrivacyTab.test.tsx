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

// A promise the test resolves by hand, so a request can be held in flight while
// the pending UI is inspected.
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const MIXED = {
  profile_photo: 'contacts',
  bio: 'nobody',
  last_seen: 'everybody',
} satisfies PrivacySettingsResult;

const ALL_OPEN = {
  profile_photo: 'everybody',
  bio: 'everybody',
  last_seen: 'everybody',
} satisfies PrivacySettingsResult;

// Distinct counts on purpose: with ok/failed/skipped all 1, swapping two count
// labels in the component still passed. The reasons are what the backend really
// sends: a gateway code on `failed`, the AccountStatus that disqualified the
// account on `skipped` (services/accounts/privacy.py), plus the keys a partial
// write already changed.
const BULK = {
  outcomes: [
    { account_id: 'acc-1', status: 'ok', error: null },
    { account_id: 'acc-5', status: 'ok', error: null },
    { account_id: 'acc-6', status: 'ok', error: null },
    { account_id: 'acc-7', status: 'ok', error: null },
    { account_id: 'acc-2', status: 'failed', error: 'account_frozen', applied: ['profile_photo'] },
    { account_id: 'acc-3', status: 'skipped', error: 'unauthorized' },
    { account_id: 'acc-4', status: 'skipped', error: 'session_error' },
  ],
  ok: 4,
  failed: 1,
  skipped: 2,
} satisfies BulkPrivacyResult;

const OPEN_ALL_BUTTON = 'Открыть фото и bio (этот аккаунт)';
const FLEET_BUTTON = 'Применить ко всем аккаунтам фермы';
const READ_FAILED = (reason: string) =>
  `Не удалось прочитать настройки приватности из Telegram (${reason})`;

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

// Each row's three radios carry the row in their accessible name, so nine
// options on the tab are all distinguishable.
function levelButton(rowLabel: string, level: string): HTMLElement {
  return within(row(rowLabel)).getByRole('radio', { name: `${rowLabel}: ${level}` });
}

test('renders the three live levels from the query response', async () => {
  routeApi({ settings: MIXED, error: null });
  renderWithClient(<PrivacyTab accountId="acc-1" />);

  expect(await screen.findByText('Фото профиля')).toBeInTheDocument();
  expect(within(row('Фото профиля')).getByText('Сейчас: Контакты')).toBeInTheDocument();
  expect(within(row('Описание (bio)')).getByText('Сейчас: Никто')).toBeInTheDocument();
  expect(within(row('Был в сети')).getByText('Сейчас: Все')).toBeInTheDocument();

  // The checked option per row is the live level, not a default.
  expect(within(row('Фото профиля')).getByRole('radio', { checked: true })).toHaveTextContent(
    'Контакты',
  );
  expect(within(row('Был в сети')).getByRole('radio', { checked: true })).toHaveTextContent('Все');
  // Same visible text, different accessible names (a11y: nine buttons in an
  // element list were indistinguishable).
  expect(levelButton('Фото профиля', 'Все')).toBeInTheDocument();
  expect(levelButton('Был в сети', 'Все')).toBeInTheDocument();
});

test('an unknown level renders as unknown, presses nothing and blocks the fleet apply', async () => {
  routeApi({
    settings: { profile_photo: 'unknown', bio: 'unknown', last_seen: 'unknown' },
    error: null,
  });
  renderWithClient(<PrivacyTab accountId="acc-1" />);

  expect(await screen.findByText('Фото профиля')).toBeInTheDocument();
  expect(within(row('Описание (bio)')).getByText('Сейчас: Неизвестно')).toBeInTheDocument();
  // The operator never picked this rule, and the note says so without claiming
  // to know where it came from — a resold account is likely, not certain.
  expect(
    within(row('Описание (bio)')).getByText(
      'Telegram вернул правило, которое дашборд не распознаёт — его выставили не отсюда; задайте уровень явно',
    ),
  ).toBeInTheDocument();
  // Nothing is preselected anywhere — an unrecognised rule must not read as "Все".
  expect(screen.queryAllByRole('radio', { checked: true })).toHaveLength(0);
  // Every key is unsendable, so an all-null 422 body cannot be fired.
  expect(screen.getByRole('button', { name: FLEET_BUTTON })).toBeDisabled();
});

test('picking a level sends only that key and adopts the re-read state', async () => {
  routeApi(
    { settings: MIXED, error: null },
    { settings: { ...MIXED, bio: 'contacts' }, error: null },
  );
  renderWithClient(<PrivacyTab accountId="acc-1" />);
  await screen.findByText('Описание (bio)');

  await userEvent.click(levelButton('Описание (bio)', 'Контакты'));

  await waitFor(() => {
    expect(requests('PUT', '/accounts/acc-1/privacy')).toHaveLength(1);
  });
  expect(await lastBody('PUT', '/accounts/acc-1/privacy')).toEqual({ bio: 'contacts' });
  // The PUT response seeds the cache, so the row reflects the new level without
  // a second GET.
  expect(await within(row('Описание (bio)')).findByText('Сейчас: Контакты')).toBeInTheDocument();
  expect(requests('GET', '/accounts/acc-1/privacy')).toHaveLength(1);
});

test('«Открыть фото и bio» sends photo and bio only, never last_seen', async () => {
  routeApi(
    { settings: MIXED, error: null },
    { settings: { ...MIXED, profile_photo: 'everybody', bio: 'everybody' }, error: null },
  );
  renderWithClient(<PrivacyTab accountId="acc-1" />);
  await screen.findByText('Фото профиля');

  await userEvent.click(screen.getByRole('button', { name: OPEN_ALL_BUTTON }));

  await waitFor(() => {
    expect(requests('PUT', '/accounts/acc-1/privacy')).toHaveLength(1);
  });
  // last_seen is absent, not null: opening it would publish the account's
  // online schedule and, by reciprocity, change what it can see.
  expect(await lastBody('PUT', '/accounts/acc-1/privacy')).toEqual({
    profile_photo: 'everybody',
    bio: 'everybody',
  });
});

test('a write whose re-read Telegram refuses keeps the rows and does not blame the read', async () => {
  const secondGet = deferred<Response>();
  let gets = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    // The write applied, but the backend's re-read was refused: 200 with
    // {settings:null, error:…}.
    if (request.method === 'PUT') {
      return Promise.resolve(jsonResponse({ settings: null, error: 'flood_wait' }));
    }
    gets += 1;
    if (gets === 1) return Promise.resolve(jsonResponse({ settings: MIXED, error: null }));
    // Hold the tab's own re-read in flight so the interim state is inspectable.
    return secondGet.promise;
  });
  renderWithClient(<PrivacyTab accountId="acc-1" />);
  await screen.findByText('Фото профиля');

  await userEvent.click(levelButton('Описание (bio)', 'Контакты'));

  // Non-destructive notice: the write is not reported as a read failure...
  expect(
    await screen.findByText(/Настройки применены, но перечитать их из Telegram не удалось/),
  ).toBeInTheDocument();
  expect(screen.queryByText(READ_FAILED('flood_wait'))).not.toBeInTheDocument();
  // ...and the tab still shows the rows and both buttons instead of going blank.
  expect(within(row('Описание (bio)')).getByText('Сейчас: Никто')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: OPEN_ALL_BUTTON })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: FLEET_BUTTON })).toBeInTheDocument();
  // The refused re-read triggered a fresh one.
  await waitFor(() => {
    expect(requests('GET', '/accounts/acc-1/privacy')).toHaveLength(2);
  });

  // Once live levels land, the "last known values" notice must not linger.
  secondGet.resolve(jsonResponse({ settings: { ...MIXED, bio: 'contacts' }, error: null }));
  expect(await within(row('Описание (bio)')).findByText('Сейчас: Контакты')).toBeInTheDocument();
  expect(
    screen.queryByText(/Настройки применены, но перечитать их из Telegram не удалось/),
  ).not.toBeInTheDocument();
});

test('a rejected write re-reads the levels instead of leaving pre-write values', async () => {
  let gets = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (request.method === 'PUT') {
      // A partial write: profile_photo landed, the next key flooded.
      return Promise.resolve(
        jsonResponse({ error: { code: 'bad_gateway', message: 'flood_wait' } }, 502),
      );
    }
    gets += 1;
    return Promise.resolve(
      jsonResponse({
        settings: gets === 1 ? MIXED : { ...MIXED, profile_photo: 'everybody' },
        error: null,
      }),
    );
  });
  renderWithClient(<PrivacyTab accountId="acc-1" />);
  await screen.findByText('Фото профиля');

  await userEvent.click(screen.getByRole('button', { name: OPEN_ALL_BUTTON }));

  expect(await within(row('Фото профиля')).findByText('Сейчас: Все')).toBeInTheDocument();
  expect(requests('GET', '/accounts/acc-1/privacy')).toHaveLength(2);
});

test('the fleet-wide apply only fires after the confirmation step', async () => {
  routeApi({ settings: MIXED, error: null });
  renderWithClient(<PrivacyTab accountId="acc-1" />);
  await screen.findByText('Фото профиля');

  await userEvent.click(screen.getByRole('button', { name: FLEET_BUTTON }));
  expect(await screen.findByText(`${FLEET_BUTTON}?`)).toBeInTheDocument();
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

test('the fleet confirm warns about a restrictive level and about losing exceptions', async () => {
  routeApi({ settings: MIXED, error: null });
  renderWithClient(<PrivacyTab accountId="acc-1" />);
  await screen.findByText('Фото профиля');

  await userEvent.click(screen.getByRole('button', { name: FLEET_BUTTON }));

  expect(
    await screen.findByText(/Внимание: среди этих уровней есть ограничивающий/),
  ).toBeInTheDocument();
  expect(screen.getByText(/Действие необратимо/)).toBeInTheDocument();
  expect(screen.getByText(/персональные исключения/)).toBeInTheDocument();
});

test('the fleet confirm does not warn when every pushed level is everybody', async () => {
  routeApi({ settings: ALL_OPEN, error: null });
  renderWithClient(<PrivacyTab accountId="acc-1" />);
  await screen.findByText('Фото профиля');

  await userEvent.click(screen.getByRole('button', { name: FLEET_BUTTON }));

  expect(await screen.findByText(`${FLEET_BUTTON}?`)).toBeInTheDocument();
  expect(screen.queryByText(/Внимание: среди этих уровней/)).not.toBeInTheDocument();
});

test('the per-account controls are locked during the sweep and the read is refreshed after it', async () => {
  const sweep = deferred<Response>();
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/privacy/all') return sweep.promise;
    return Promise.resolve(jsonResponse({ settings: MIXED, error: null }));
  });
  renderWithClient(<PrivacyTab accountId="acc-1" />);
  await screen.findByText('Фото профиля');

  await userEvent.click(screen.getByRole('button', { name: FLEET_BUTTON }));
  await userEvent.click(await screen.findByRole('button', { name: 'Применить' }));
  await waitFor(() => {
    expect(requests('POST', '/accounts/privacy/all')).toHaveLength(1);
  });

  // The sweep writes to this account too, so a row click mid-sweep would
  // diverge from what the sweep is about to set.
  expect(levelButton('Фото профиля', 'Все')).toBeDisabled();
  expect(levelButton('Был в сети', 'Никто')).toBeDisabled();
  expect(screen.getByRole('button', { name: OPEN_ALL_BUTTON })).toBeDisabled();
  // The dialog can be dismissed with Escape while the sweep runs for minutes,
  // so the button label carries the only remaining trace of it.
  expect(screen.getByRole('button', { name: 'Применяем ко всей ферме…' })).toBeDisabled();

  sweep.resolve(jsonResponse(BULK));

  // This account's levels are now whatever the sweep set — re-read them.
  await waitFor(() => {
    expect(requests('GET', '/accounts/acc-1/privacy')).toHaveLength(2);
  });
  expect(levelButton('Фото профиля', 'Все')).toBeEnabled();
});

test('the fleet button is locked while a per-account write is in flight', async () => {
  const put = deferred<Response>();
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (request.method === 'PUT') return put.promise;
    return Promise.resolve(jsonResponse({ settings: MIXED, error: null }));
  });
  renderWithClient(<PrivacyTab accountId="acc-1" />);
  await screen.findByText('Фото профиля');

  await userEvent.click(levelButton('Описание (bio)', 'Контакты'));

  await waitFor(() => {
    expect(screen.getByRole('button', { name: FLEET_BUTTON })).toBeDisabled();
  });
  put.resolve(jsonResponse({ settings: { ...MIXED, bio: 'contacts' }, error: null }));
  await waitFor(() => {
    expect(screen.getByRole('button', { name: FLEET_BUTTON })).toBeEnabled();
  });
});

test('the fleet result shows the counts, the failures and why accounts were skipped', async () => {
  routeApi({ settings: MIXED, error: null });
  renderWithClient(<PrivacyTab accountId="acc-1" />);
  await screen.findByText('Фото профиля');

  await userEvent.click(screen.getByRole('button', { name: FLEET_BUTTON }));
  await userEvent.click(await screen.findByRole('button', { name: 'Применить' }));

  expect(await screen.findByText('Применено: 4')).toBeInTheDocument();
  expect(screen.getByText('Ошибок: 1')).toBeInTheDocument();
  expect(screen.getByText('Пропущено: 2')).toBeInTheDocument();
  // A failure is inspectable, not swallowed into the count — and the reason is
  // TRANSLATED: these values are stable backend codes, and the report used to
  // read "acc-2 — account_frozen". A `failed` row also names the keys that DID
  // land before the refusal: setPrivacy is one call per key with no rollback, so
  // this account's avatar is already public despite the failure.
  const list = screen.getByRole('list');
  expect(
    within(list).getByText(
      'acc-2 — Аккаунт заморожен Telegram — редактирование недоступно · уже изменено в Telegram: Фото профиля',
    ),
  ).toBeInTheDocument();
  // ...and so is a skip: "2 skipped" without the accounts and their status is
  // not actionable.
  expect(within(list).getByText('acc-3 — пропущен (Не авторизован)')).toBeInTheDocument();
  expect(within(list).getByText('acc-4 — пропущен (Ошибка сессии)')).toBeInTheDocument();
  // Accounts that succeeded are NOT in the problem list.
  expect(within(list).queryByText(/acc-1/)).not.toBeInTheDocument();
  expect(within(list).queryByText(/acc-5/)).not.toBeInTheDocument();
  expect(within(list).getAllByRole('listitem')).toHaveLength(3);
});

test('a flooded fleet row shows the real wait, not «повторите через ? с»', async () => {
  // `retry_after_seconds` is the wait Telegram actually mandated, carried on the
  // outcome. An earlier round substituted '?' because no payload had a duration;
  // the schema carries one now, and "retry in ? s" is advice the operator cannot
  // act on. The other reasonText call sites (a refused read, a refused write
  // re-read) have no such field and keep the '?' — see the `noReason` tests.
  const flooded = {
    outcomes: [
      { account_id: 'acc-2', status: 'failed', error: 'flood_wait', retry_after_seconds: 30 },
      // A non-flood refusal in the same report proves the fallback is untouched:
      // account_frozen interpolates no duration at all.
      { account_id: 'acc-3', status: 'failed', error: 'account_frozen' },
    ],
    ok: 0,
    failed: 2,
    skipped: 0,
  } satisfies BulkPrivacyResult;
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/privacy/all') return Promise.resolve(jsonResponse(flooded));
    if (pathname === '/api/v1/accounts/acc-1/privacy') {
      return Promise.resolve(jsonResponse({ settings: MIXED, error: null }));
    }
    return Promise.resolve(jsonResponse({}));
  });
  renderWithClient(<PrivacyTab accountId="acc-1" />);
  await screen.findByText('Фото профиля');

  await userEvent.click(screen.getByRole('button', { name: FLEET_BUTTON }));
  await userEvent.click(await screen.findByRole('button', { name: 'Применить' }));

  const list = await screen.findByRole('list');
  expect(
    within(list).getByText('acc-2 — Telegram ограничил действия — повторите через 30 с'),
  ).toBeInTheDocument();
  expect(
    within(list).getByText('acc-3 — Аккаунт заморожен Telegram — редактирование недоступно'),
  ).toBeInTheDocument();
});

test('a new write clears the previous fleet report', async () => {
  routeApi(
    { settings: MIXED, error: null },
    { settings: { ...MIXED, bio: 'contacts' }, error: null },
  );
  renderWithClient(<PrivacyTab accountId="acc-1" />);
  await screen.findByText('Фото профиля');

  await userEvent.click(screen.getByRole('button', { name: FLEET_BUTTON }));
  await userEvent.click(await screen.findByRole('button', { name: 'Применить' }));
  expect(await screen.findByText('Применено: 4')).toBeInTheDocument();

  await userEvent.click(levelButton('Описание (bio)', 'Контакты'));

  // Those counts describe a state this write just replaced.
  await waitFor(() => {
    expect(screen.queryByText('Применено: 4')).not.toBeInTheDocument();
  });
});

test('a refused read renders the reason instead of an empty form', async () => {
  routeApi({ settings: null, error: 'flood_wait' });
  renderWithClient(<PrivacyTab accountId="acc-1" />);

  // The banner used to interpolate the raw code; the reason is a code table
  // entry, so it is translated (the duration is not in this payload, hence '?').
  expect(
    await screen.findByText(READ_FAILED('Telegram ограничил действия — повторите через ? с')),
  ).toBeInTheDocument();
  expect(screen.queryByText('Фото профиля')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: OPEN_ALL_BUTTON })).not.toBeInTheDocument();
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
  expect(await screen.findByText(READ_FAILED('privacy_read_failed'))).toBeInTheDocument();

  failing = false;
  await userEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  expect(await screen.findByText('Фото профиля')).toBeInTheDocument();
});

test('a 500 that is not our error envelope still renders a banner and a retry', async () => {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(jsonResponse({ detail: 'Internal Server Error' }, 500)),
  );
  renderWithClient(<PrivacyTab accountId="acc-1" />);

  // No envelope means no code, but the tab must never be just the hint text.
  const banner = await screen.findByRole('alert');
  expect(banner).toHaveTextContent(READ_FAILED('причина неизвестна'));
  expect(within(banner).getByRole('button', { name: 'Повторить' })).toBeInTheDocument();
});

test('a rejected fetch still renders a banner and a retry', async () => {
  vi.mocked(fetch).mockImplementation(() => Promise.reject(new TypeError('Failed to fetch')));
  renderWithClient(<PrivacyTab accountId="acc-1" />);

  const banner = await screen.findByRole('alert');
  expect(banner).toHaveTextContent(READ_FAILED('причина неизвестна'));
  expect(within(banner).getByRole('button', { name: 'Повторить' })).toBeInTheDocument();
});

test('the fleet apply is blocked while a read error is showing over stale rows', async () => {
  let gets = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (request.method === 'PUT') {
      return Promise.resolve(jsonResponse({ detail: 'boom' }, 500));
    }
    gets += 1;
    // The first read succeeds; the re-read after the failed write does not
    // (staleTime is 0, so this is the tab-away-and-back case too).
    if (gets === 1) return Promise.resolve(jsonResponse({ settings: MIXED, error: null }));
    return Promise.resolve(jsonResponse({ detail: 'boom' }, 500));
  });
  renderWithClient(<PrivacyTab accountId="acc-1" />);
  await screen.findByText('Фото профиля');

  await userEvent.click(levelButton('Описание (bio)', 'Контакты'));

  expect(await screen.findByRole('alert')).toHaveTextContent(READ_FAILED('причина неизвестна'));
  // The rows co-render with the banner, and they are stale: pushing them to the
  // whole fleet is exactly the accident to prevent.
  expect(within(row('Описание (bio)')).getByText('Сейчас: Никто')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: FLEET_BUTTON })).toBeDisabled();
});
