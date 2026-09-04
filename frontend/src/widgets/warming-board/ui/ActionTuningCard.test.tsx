import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { ActionTuningCard } from './ActionTuningCard';

const SETTINGS = {
  inter_account_chat: false,
  reactions_enabled: true,
  join_enabled: true,
  enforce_readiness: true,
  has_gemini_key: false,
  gemini_model: 'gemini-2.5-flash',
  // Operator-set on the settings page. A PUT is a full replacement and these two
  // columns have no keep-semantics on the write path, so whatever this card omits
  // is reset to the schema defaults (1 and 0.0).
  gemini_max_retries: 4,
  gemini_min_interval_seconds: 2.5,
  updated_at: 'now',
};

// The card's own counts, derived the same way the legend derives them. Written out
// so a row that changes state has to change this number too — that is the point of
// the states being data rather than markup.
const SOON_ROWS = 22;
const ALWAYS_ROWS = 5;
// Thirty actions plus the readiness gate, which is not one.
const ALL_SWITCHES = 31;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function routeApi(settings: Record<string, unknown> = SETTINGS, putStatus = 200) {
  let reads = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/warming/settings') {
      if (request.method === 'PUT' && putStatus !== 200) {
        // With a `message` — an envelope without one falls back to the generic
        // shell.mutationError, which is the very branch that hides whether the
        // alert reports the specific reason at all.
        return Promise.resolve(
          jsonResponse(
            { error: { code: 'internal_error', message: 'settings_row_locked' } },
            putStatus,
          ),
        );
      }
      // Every GET carries a fresh `updated_at`, exactly as the real singleton row
      // does — it is rewritten on every save, and a refetch happens on a reconnect
      // too. A byte-identical row would let React Query's structural sharing keep
      // the object identity, which hides anything keyed on it.
      reads += 1;
      return Promise.resolve(jsonResponse({ ...settings, updated_at: `read-${String(reads)}` }));
    }
    return Promise.resolve(jsonResponse({}));
  });
}

// How many times the card has READ the settings row.
function settingsReads(): number {
  return vi
    .mocked(fetch)
    .mock.calls.filter(
      ([input]) =>
        new URL((input as Request).url).pathname === '/api/v1/warming/settings' &&
        (input as Request).method !== 'PUT',
    ).length;
}

// The body of the PUT the card sent.
async function savedBody(): Promise<Record<string, unknown>> {
  let call: [unknown, ...unknown[]] | undefined;
  await waitFor(() => {
    call = vi
      .mocked(fetch)
      .mock.calls.find(
        ([input]) =>
          new URL((input as Request).url).pathname === '/api/v1/warming/settings' &&
          (input as Request).method === 'PUT',
      );
    expect(call).toBeDefined();
  });
  return (await (call![0] as Request).clone().json()) as Record<string, unknown>;
}

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

// The card is collapsed on the page like every other settings card beside it, and a
// collapsed body carries `hidden` — which takes it out of the a11y tree, so no
// `getByRole('switch')` resolves until it is open. The chevron button's accessible
// name is the bare title; the header button's also carries the subtitle.
async function openCard(): Promise<void> {
  await userEvent.click(screen.getByRole('button', { name: 'Тонкая настройка действий' }));
}

test('every group and every action row is on the card once it is open', async () => {
  routeApi();
  renderWithClient(<ActionTuningCard />);
  await openCard();

  for (const group of [
    'Чтение',
    'Активность',
    'Развлечения',
    'Социальные',
    'Группы и чаты',
    'Профиль',
  ]) {
    expect(screen.getByText(group)).toBeInTheDocument();
  }
  expect(screen.getAllByRole('switch')).toHaveLength(ALL_SWITCHES);
  // One row out of each state, named as the operator reads it.
  expect(screen.getByRole('switch', { name: 'Реакции на посты' })).toBeInTheDocument();
  expect(screen.getByRole('switch', { name: 'Прокрутка каналов' })).toBeInTheDocument();
  expect(screen.getByRole('switch', { name: 'Голосование в опросах' })).toBeInTheDocument();
  // The traffic warning sits on the group, not on its rows.
  expect(screen.getByText('много трафика')).toBeInTheDocument();
});

test('an action with no gateway refuses instead of pretending: off, locked, "скоро"', async () => {
  routeApi();
  renderWithClient(<ActionTuningCard />);
  await openCard();

  const soon = screen.getByRole('switch', { name: 'Поиск GIF' });
  expect(soon).toHaveAttribute('aria-checked', 'false');
  expect(soon).toBeDisabled();
  expect(screen.getAllByText('скоро')).toHaveLength(SOON_ROWS);
});

test('a core-cycle action reads as on and cannot be moved', async () => {
  routeApi();
  renderWithClient(<ActionTuningCard />);
  await openCard();

  // `read_channel` is the cycle itself and `watch_peer_stories` is keyed off the
  // server config, so neither has a settings column to write. On, and locked on.
  for (const name of ['Отметить как прочитанное', 'Просмотр историй', 'Симуляция печати']) {
    const row = screen.getByRole('switch', { name });
    expect(row).toHaveAttribute('aria-checked', 'true');
    expect(row).toBeDisabled();
  }
  expect(screen.getAllByText('всегда')).toHaveLength(ALWAYS_ROWS);
});

test('the legend counts the table, so a connected action needs no copy change', async () => {
  routeApi();
  renderWithClient(<ActionTuningCard />);
  await openCard();

  expect(
    screen.getByText(`работает · ${String(ALL_SWITCHES - 1 - SOON_ROWS)}`),
  ).toBeInTheDocument();
  expect(screen.getByText(`скоро · ${String(SOON_ROWS)}`)).toBeInTheDocument();
});

test('save writes the three stored toggles and the readiness gate', async () => {
  routeApi();
  renderWithClient(<ActionTuningCard />);
  await openCard();

  await waitFor(() => {
    expect(screen.getByRole('switch', { name: 'Переписка между аккаунтами' })).toHaveAttribute(
      'aria-checked',
      'false',
    );
  });
  await userEvent.click(screen.getByRole('switch', { name: 'Переписка между аккаунтами' }));
  await userEvent.click(screen.getByText('Сохранить'));

  const body = await savedBody();
  expect(body.inter_account_chat).toBe(true);
  expect(body.reactions_enabled).toBe(true);
  expect(body.join_enabled).toBe(true);
  expect(body.enforce_readiness).toBe(true);
});

test('"Выключить все" reaches only the actions the backend can store', async () => {
  routeApi();
  renderWithClient(<ActionTuningCard />);
  await openCard();

  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeEnabled();
  });
  await userEvent.click(screen.getByText('Выключить все'));

  // The three live rows go off…
  for (const name of ['Реакции на посты', 'Вступление в каналы', 'Переписка между аккаунтами']) {
    expect(screen.getByRole('switch', { name })).toHaveAttribute('aria-checked', 'false');
  }
  // …the locked ones do not move, and the gate is not an action.
  expect(screen.getByRole('switch', { name: 'Просмотр историй' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
  expect(screen.getByRole('switch', { name: 'Гейт готовности' })).toHaveAttribute(
    'aria-checked',
    'true',
  );

  await userEvent.click(screen.getByText('Сохранить'));
  const body = await savedBody();
  expect(body.reactions_enabled).toBe(false);
  expect(body.join_enabled).toBe(false);
  expect(body.inter_account_chat).toBe(false);
  expect(body.enforce_readiness).toBe(true);
});

test('"Включить все" turns the stored actions back on', async () => {
  routeApi({ ...SETTINGS, reactions_enabled: false, join_enabled: false });
  renderWithClient(<ActionTuningCard />);
  await openCard();

  await waitFor(() => {
    expect(screen.getByRole('switch', { name: 'Реакции на посты' })).toHaveAttribute(
      'aria-checked',
      'false',
    );
  });
  await userEvent.click(screen.getByText('Включить все'));
  await userEvent.click(screen.getByText('Сохранить'));

  const body = await savedBody();
  expect(body.reactions_enabled).toBe(true);
  expect(body.join_enabled).toBe(true);
  expect(body.inter_account_chat).toBe(true);
});

test('the readiness gate keeps its own switch and its own value', async () => {
  routeApi();
  renderWithClient(<ActionTuningCard />);
  await openCard();

  await waitFor(() => {
    expect(screen.getByRole('switch', { name: 'Гейт готовности' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
  });
  await userEvent.click(screen.getByRole('switch', { name: 'Гейт готовности' }));
  await userEvent.click(screen.getByText('Сохранить'));

  const body = await savedBody();
  expect(body.enforce_readiness).toBe(false);
  // Flipping the gate does not touch the actions beside it.
  expect(body.reactions_enabled).toBe(true);
});

test('save leaves the settings-page Gemini fields out of the body entirely', async () => {
  routeApi();
  renderWithClient(<ActionTuningCard />);
  await openCard();

  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeEnabled();
  });
  await userEvent.click(screen.getByText('Сохранить'));

  // The write path keeps all three on an omitted field, so absence is what
  // preserves them. Echoing the query's values instead writes whatever this card
  // last read — and it never refetches on window focus, so a tab left open would
  // overwrite the settings page with a stale row.
  const body = await savedBody();
  expect(body).not.toHaveProperty('gemini_max_retries');
  expect(body).not.toHaveProperty('gemini_min_interval_seconds');
  expect(body).not.toHaveProperty('gemini_model');
});

test('a cold cache saves the stored toggles, not the hardcoded fallbacks', async () => {
  // Reactions off + mutual chat on is the exact inverse of the fallbacks the card
  // renders before the row arrives, so a state left on those cannot pass by accident.
  routeApi({ ...SETTINGS, reactions_enabled: false, inter_account_chat: true });
  renderWithClient(<ActionTuningCard />);
  await openCard();

  await waitFor(() => {
    expect(screen.getByRole('switch', { name: 'Реакции на посты' })).toHaveAttribute(
      'aria-checked',
      'false',
    );
  });
  await userEvent.click(screen.getByText('Сохранить'));

  const body = await savedBody();
  expect(body.reactions_enabled).toBe(false);
  expect(body.inter_account_chat).toBe(true);
});

test('a rejected save reports the envelope reason and keeps the operator input', async () => {
  routeApi(SETTINGS, 500);
  renderWithClient(<ActionTuningCard />);
  await openCard();

  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeEnabled();
  });
  await userEvent.click(screen.getByRole('switch', { name: 'Переписка между аккаунтами' }));
  await userEvent.click(screen.getByText('Сохранить'));

  // The alert reports the envelope's OWN reason, not the generic copy: the card
  // stays on screen over the failure, so it is the in-context report.
  await waitFor(() => {
    expect(screen.getByRole('alert')).toHaveTextContent('settings_row_locked');
  });
  // onSettled invalidates the settings key regardless of outcome, so a GET follows
  // the rejected PUT. Wait for that row to land: re-seeding the toggles from it
  // would revert the flip while the error is still on screen.
  await waitFor(() => {
    expect(settingsReads()).toBeGreaterThan(1);
  });
  expect(screen.getByRole('switch', { name: 'Переписка между аккаунтами' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
});

test('saving invalidates the settings and the board, not the whole cache', async () => {
  routeApi();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  render(
    <QueryClientProvider client={queryClient}>
      <ActionTuningCard />
    </QueryClientProvider>,
  );
  await openCard();

  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeEnabled();
  });
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(invalidate).toHaveBeenCalled();
  });
  // An unfiltered invalidateQueries() also refetches the accounts table, the
  // proxies, the neurocomment campaigns and every open profile snapshot.
  for (const [filters] of invalidate.mock.calls) {
    expect(filters?.queryKey).toBeDefined();
  }
});

test('save is blocked until the stored row lands', async () => {
  // A never-resolving GET: the button must not offer to write the fallbacks.
  vi.mocked(fetch).mockImplementation(() => new Promise<Response>(() => undefined));
  renderWithClient(<ActionTuningCard />);
  await openCard();

  expect(screen.getByText('Сохранить')).toBeDisabled();
});
