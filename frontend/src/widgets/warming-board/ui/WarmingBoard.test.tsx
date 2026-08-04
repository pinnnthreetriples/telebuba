import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { WarmingAccountState } from '@/shared/api';

import { WarmingBoard } from './WarmingBoard';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

const NONE_BUSY = new Set<string>();

function account(id: string, state: WarmingAccountState['state']): WarmingAccountState {
  return { account_id: id, label: id, state, health: 'ok', cycles_completed: 2, trust_score: 70 };
}

function warmed(id: string, days: number, target: number): WarmingAccountState {
  return {
    account_id: id,
    label: id,
    state: 'active',
    health: 'ok',
    cycles_completed: 4,
    warming_days: days,
    target_days: target,
  };
}

const WARMING = [account('79051184490', 'active'), account('79161234567', 'sleeping')];

test('busy state is per card, and more than one card can be busy', () => {
  // A single busy id could not say "card 2 is busy, card 1 is not" once a second
  // card had been acted on: the pool button alone starts one call per account.
  const { unmount } = renderWithClient(
    <WarmingBoard
      warming={WARMING}
      onStop={vi.fn()}
      onPromote={vi.fn()}
      busyIds={new Set(['79161234567'])}
    />,
  );
  expect(screen.getAllByText('Стоп')[0]).toBeEnabled();
  expect(screen.getAllByText('Стоп')[1]).toBeDisabled();
  unmount();

  renderWithClient(
    <WarmingBoard
      warming={WARMING}
      onStop={vi.fn()}
      onPromote={vi.fn()}
      busyIds={new Set(['79051184490', '79161234567'])}
    />,
  );
  for (const button of screen.getAllByText('Стоп')) {
    expect(button).toBeDisabled();
  }
});

test('renders an in-progress card per warming account with the stage labels', () => {
  renderWithClient(
    <WarmingBoard warming={WARMING} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  expect(screen.getByText('79051184490')).toBeInTheDocument();
  expect(screen.getByText('79161234567')).toBeInTheDocument();
  expect(screen.getAllByText('Подписка').length).toBeGreaterThan(0);
  // Rail steps mirror the real cycle order (reactions before stories); the old
  // decorative "Отчёт" step is gone.
  expect(screen.getAllByText('Реакции').length).toBeGreaterThan(0);
  expect(screen.getAllByText('Сторис').length).toBeGreaterThan(0);
  expect(screen.queryByText('Отчёт')).not.toBeInTheDocument();
});

test('shows a pre-start hold with a countdown, not a fake subscribe step', () => {
  const held: WarmingAccountState = {
    account_id: '79990001122',
    label: '79990001122',
    state: 'active',
    health: 'ok',
    cycles_completed: 0,
    next_run_at: new Date(Date.now() + 3_600_000).toISOString(),
  };
  renderWithClient(
    <WarmingBoard warming={[held]} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  expect(screen.getByText('Выдержка перед стартом')).toBeInTheDocument();
  // The misleading "actively subscribing" activity text is gone during the hold…
  expect(screen.queryByText('Подписка на каналы')).not.toBeInTheDocument();
  // …and the real wait shows a live countdown.
  expect(screen.getByText(/ещё/)).toBeInTheDocument();
});

test('stops the clicked account', async () => {
  const onStop = vi.fn();
  renderWithClient(
    <WarmingBoard warming={WARMING} onStop={onStop} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  await userEvent.click(screen.getAllByText('Стоп')[0]!);
  await userEvent.click(screen.getByText('Остановить'));
  expect(onStop).toHaveBeenCalledWith('79051184490');
});

test('auto-completes at the per-account target and promotes via the finish button', async () => {
  const onPromote = vi.fn();
  // 3 elapsed days against a chosen target of 3 → the card flips to complete
  // even though cycles_completed is well under the old hardcoded 14.
  const done = warmed('79051184490', 3, 3);
  renderWithClient(
    <WarmingBoard warming={[done]} onStop={vi.fn()} onPromote={onPromote} busyIds={NONE_BUSY} />,
  );
  await userEvent.click(screen.getByText('Отправить в прогретые'));
  expect(onPromote).toHaveBeenCalledWith('79051184490');
});

test('shows the per-account target as the day-progress denominator, not a hardcoded 14', () => {
  renderWithClient(
    <WarmingBoard
      warming={[warmed('79051184490', 6, 7)]}
      onStop={vi.fn()}
      onPromote={vi.fn()}
      busyIds={NONE_BUSY}
    />,
  );
  expect(screen.getByText('6 / 7 дней')).toBeInTheDocument();
  expect(screen.queryByText(/\/ 14 дней/)).not.toBeInTheDocument();
});

test('pluralizes the Russian day noun by the target (день / дня / дней)', () => {
  const { rerender } = renderWithClient(
    <WarmingBoard
      warming={[warmed('a', 0, 1)]}
      onStop={vi.fn()}
      onPromote={vi.fn()}
      busyIds={NONE_BUSY}
    />,
  );
  expect(screen.getByText('0 / 1 день')).toBeInTheDocument();

  rerender(
    <QueryClientProvider client={new QueryClient()}>
      <WarmingBoard
        warming={[warmed('a', 1, 3)]}
        onStop={vi.fn()}
        onPromote={vi.fn()}
        busyIds={NONE_BUSY}
      />
    </QueryClientProvider>,
  );
  expect(screen.getByText('1 / 3 дня')).toBeInTheDocument();
});

test('keeps an account in progress below its target', () => {
  renderWithClient(
    <WarmingBoard
      warming={[warmed('79051184490', 2, 7)]}
      onStop={vi.fn()}
      onPromote={vi.fn()}
      busyIds={NONE_BUSY}
    />,
  );
  // Below target: still shows the stop control and stage rail, not the finish button.
  expect(screen.getByText('Стоп')).toBeInTheDocument();
  expect(screen.queryByText('Отправить в прогретые')).not.toBeInTheDocument();
});

test('shows a success or error mark from the feedback map', () => {
  const { rerender } = renderWithClient(
    <WarmingBoard
      warming={WARMING}
      onStop={vi.fn()}
      onPromote={vi.fn()}
      busyIds={NONE_BUSY}
      feedback={{ '79051184490': 'ok' }}
    />,
  );
  expect(document.querySelector('.text-success svg')).toBeInTheDocument();

  rerender(
    <QueryClientProvider client={new QueryClient()}>
      <WarmingBoard
        warming={WARMING}
        onStop={vi.fn()}
        onPromote={vi.fn()}
        busyIds={NONE_BUSY}
        feedback={{ '79051184490': 'err' }}
      />
    </QueryClientProvider>,
  );
  expect(document.querySelector('.text-danger svg')).toBeInTheDocument();
});

test('renders the Telegram display name when first/last name are present', () => {
  const named: WarmingAccountState = {
    ...account('a1', 'active'),
    first_name: 'Vika',
    last_name: 'Ix',
    phone: '+79215532011',
  };
  renderWithClient(
    <WarmingBoard warming={[named]} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  // The name wins over the phone/id, matching the Accounts table.
  expect(screen.getByText('Vika Ix')).toBeInTheDocument();
  expect(screen.queryByText('+79215532011')).not.toBeInTheDocument();
});

test('falls back to the phone as the card id when there is no name', () => {
  const withPhone: WarmingAccountState = {
    ...account('a1', 'active'),
    label: 'Label A',
    phone: '+79215532011',
  };
  renderWithClient(
    <WarmingBoard warming={[withPhone]} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  expect(screen.getByText('+79215532011')).toBeInTheDocument();
  expect(screen.queryByText('Label A')).not.toBeInTheDocument();
});

test('the "?" tooltip shows the ЛС (DM) line from dm_allowed', () => {
  const allowed: WarmingAccountState = { ...account('a1', 'active'), dm_allowed: true };
  const closed: WarmingAccountState = { ...account('a2', 'active'), dm_allowed: false };
  renderWithClient(
    <WarmingBoard
      warming={[allowed, closed]}
      onStop={vi.fn()}
      onPromote={vi.fn()}
      busyIds={NONE_BUSY}
    />,
  );
  expect(screen.getByText('ЛС: разрешены')).toBeInTheDocument();
  expect(screen.getByText('ЛС: закрыты')).toBeInTheDocument();
});

test('the actions counter reflects daily_actions / daily_cap', () => {
  const acc: WarmingAccountState = {
    ...account('a1', 'active'),
    daily_actions: 6,
    daily_cap: 18,
  };
  renderWithClient(
    <WarmingBoard warming={[acc]} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  // Uses the served cap, not the old hardcoded /10.
  expect(screen.getByText('6/18')).toBeInTheDocument();
});

test('the per-card log request uses the served card_log_limit', async () => {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(jsonResponse({ items: [], next_cursor: null })),
  );
  renderWithClient(
    <WarmingBoard
      warming={[account('79051184490', 'active')]}
      onStop={vi.fn()}
      onPromote={vi.fn()}
      busyIds={NONE_BUSY}
      logLimit={7}
    />,
  );
  await userEvent.click(screen.getByText('Лог активности'));
  await waitFor(() => {
    const used = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('limit=7'));
    expect(used).toBe(true);
  });
});

test('the per-card log request scopes itself to warming and spam-status events', async () => {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(jsonResponse({ items: [], next_cursor: null })),
  );
  renderWithClient(
    <WarmingBoard
      warming={[account('79051184490', 'active')]}
      onStop={vi.fn()}
      onPromote={vi.fn()}
      busyIds={NONE_BUSY}
    />,
  );
  await userEvent.click(screen.getByText('Лог активности'));
  await waitFor(() => {
    const scoped = vi.mocked(fetch).mock.calls.some(([input]) => {
      const url = new URL((input as Request).url);
      return (
        url.pathname === '/api/v1/logs' &&
        // `warming_` covers the engine's own gateway rows (`warming_telegram_*`);
        // a bare `telegram_*` row belongs to another domain and must not leak in.
        url.searchParams.get('event_prefix') === 'warming_,spam_status'
      );
    });
    expect(scoped).toBe(true);
  });
});

test('expanding a card fetches that account real activity log', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/logs') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              id: 1,
              created_at: '2026-06-30T12:04:00+00:00',
              level: 'INFO',
              status: 'success',
              account_id: '79051184490',
              event: 'warming_subscribe',
            },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });

  renderWithClient(
    <WarmingBoard warming={WARMING} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  await userEvent.click(screen.getAllByText('Лог активности')[0]!);
  await waitFor(() => {
    // Event codes are localized on the client (not shown as raw snake_case).
    expect(screen.getByText('Подписка на канал')).toBeInTheDocument();
  });
  const fetched = vi
    .mocked(fetch)
    .mock.calls.some(([input]) => (input as Request).url.includes('account_id=79051184490'));
  expect(fetched).toBe(true);
});

test('names which channel and which reaction each log action touched', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/logs') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              id: 1,
              created_at: '2026-06-30T12:04:00+00:00',
              level: 'INFO',
              status: 'success',
              account_id: '79051184490',
              event: 'warming_telegram_join_channel',
              extra: { channel: '@durov' },
            },
            {
              id: 2,
              created_at: '2026-06-30T12:05:00+00:00',
              level: 'INFO',
              status: 'success',
              account_id: '79051184490',
              event: 'warming_telegram_react_to_post',
              extra: { channel: '@news', reaction: '🔥' },
            },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });

  renderWithClient(
    <WarmingBoard
      warming={[account('79051184490', 'active')]}
      onStop={vi.fn()}
      onPromote={vi.fn()}
      busyIds={NONE_BUSY}
      channelLabels={{ '@durov': 'Дуров' }}
    />,
  );
  await userEvent.click(screen.getByText('Лог активности'));
  await waitFor(() => {
    // The join line names the channel by its configured label…
    expect(screen.getByText('Дуров')).toBeInTheDocument();
  });
  // …and the react line shows the (unlabelled) handle plus the emoji actually placed.
  expect(screen.getByText('@news')).toBeInTheDocument();
  expect(screen.getByText('🔥')).toBeInTheDocument();
});

test('shows reaction-skip reasons and stories-seen detail in the log', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/logs') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              id: 1,
              created_at: '2026-06-30T12:00:00+00:00',
              level: 'INFO',
              status: 'success',
              account_id: '79051184490',
              event: 'warming_reaction_skipped',
              extra: { channel: '@news', reason: 'chance' },
            },
            {
              id: 2,
              created_at: '2026-06-30T12:01:00+00:00',
              level: 'INFO',
              status: 'success',
              account_id: '79051184490',
              event: 'warming_telegram_watch_peer_stories',
              extra: { peer: '@durov', stories_seen: 3 },
            },
            {
              id: 3,
              created_at: '2026-06-30T12:02:00+00:00',
              level: 'INFO',
              status: 'success',
              account_id: '79051184490',
              event: 'warming_telegram_watch_peer_stories',
              extra: { peer: '@news', stories_seen: 0 },
            },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });

  renderWithClient(
    <WarmingBoard
      warming={[account('79051184490', 'active')]}
      onStop={vi.fn()}
      onPromote={vi.fn()}
      busyIds={NONE_BUSY}
    />,
  );
  await userEvent.click(screen.getByText('Лог активности'));
  await waitFor(() => {
    expect(screen.getByText('Реакция не поставлена')).toBeInTheDocument();
  });
  // "chance didn't hit" breadcrumb, and the two honest stories outcomes.
  expect(screen.getByText('· не выпал шанс')).toBeInTheDocument();
  expect(screen.getByText('· просмотрено историй: 3')).toBeInTheDocument();
  expect(screen.getByText('· нет активных историй')).toBeInTheDocument();
});

test('says what Telegram refused on a failed warming action', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/logs') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              id: 1,
              created_at: '2026-06-30T12:00:00+00:00',
              level: 'ERROR',
              status: 'error',
              account_id: '79051184490',
              event: 'warming_telegram_join_channel_failed',
              extra: { channel: '@news', error_type: 'ChannelPrivateError' },
            },
            {
              id: 2,
              created_at: '2026-06-30T12:01:00+00:00',
              level: 'WARNING',
              status: 'warning',
              account_id: '79051184490',
              event: 'warming_telegram_action_unavailable',
              extra: { action_type: 'read_channel', error_type: 'session_dead' },
            },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });

  renderWithClient(
    <WarmingBoard
      warming={[account('79051184490', 'error')]}
      onStop={vi.fn()}
      onPromote={vi.fn()}
      busyIds={NONE_BUSY}
    />,
  );
  await userEvent.click(screen.getByText('Лог активности'));
  // A Telethon class name (logEventTelegram.error.*) and a gateway stable code
  // (accounts.profile.code.*) both reach the operator as the real refusal, where
  // the card used to show the action label and nothing else.
  await waitFor(() => {
    expect(screen.getByText('· чат закрыт: не пускают или выгнали')).toBeInTheDocument();
  });
  expect(screen.getByText('· Сессия аккаунта в Telegram недействительна')).toBeInTheDocument();
});

test('explains a cycle that ended badly, and stays quiet about a healthy one', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/logs') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              id: 1,
              created_at: '2026-06-30T12:00:00+00:00',
              level: 'WARNING',
              status: 'warning',
              account_id: '79051184490',
              event: 'warming_cycle_completed',
              extra: { status: 'peer_flood', failures: 1 },
            },
            {
              id: 2,
              created_at: '2026-06-30T11:00:00+00:00',
              level: 'INFO',
              status: 'success',
              account_id: '79051184490',
              event: 'warming_cycle_completed',
              extra: { status: 'ok', reads: 2 },
            },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });

  renderWithClient(
    <WarmingBoard
      warming={[account('79051184490', 'active')]}
      onStop={vi.fn()}
      onPromote={vi.fn()}
      busyIds={NONE_BUSY}
    />,
  );
  await userEvent.click(screen.getByText('Лог активности'));
  // The engine writes a cycle's outcome as extra.status; only the bad ones have a
  // label, so a normal cycle keeps its one-line "завершён" instead of gaining a tail.
  await waitFor(() => {
    expect(screen.getByText('· флуд-ограничение')).toBeInTheDocument();
  });
  expect(screen.getAllByText('Цикл прогрева завершён')).toHaveLength(2);
  expect(screen.queryByText(/· ok/)).not.toBeInTheDocument();
});

test('clear button hides the existing log lines', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/logs') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              id: 1,
              created_at: '2026-06-30T12:04:00+00:00',
              level: 'INFO',
              status: 'success',
              account_id: '79051184490',
              event: 'warming_subscribe',
            },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });

  renderWithClient(
    <WarmingBoard warming={WARMING} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  await userEvent.click(screen.getAllByText('Лог активности')[0]!);
  await waitFor(() => {
    expect(screen.getByText('Подписка на канал')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Очистить'));
  await waitFor(() => {
    expect(screen.queryByText('Подписка на канал')).not.toBeInTheDocument();
  });
});

test('an active account shows the real in-cycle step from last_action, not a cycle-count guess', () => {
  const reading: WarmingAccountState = { ...account('a1', 'active'), last_action: 'read' };
  renderWithClient(
    <WarmingBoard warming={[reading]} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  expect(screen.getByText('Чтение ленты постов')).toBeInTheDocument();
});

test('shows the reaction step and never the removed "report" activity', () => {
  // cycles_completed=5 mapped to "report" under the old cycles%6 formula; the
  // real in-cycle action is a reaction, and the report activity no longer exists.
  const running: WarmingAccountState = {
    ...account('a1', 'active'),
    cycles_completed: 5,
    last_action: 'react',
  };
  renderWithClient(
    <WarmingBoard warming={[running]} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  expect(screen.getByText('Поставлены реакции')).toBeInTheDocument();
  expect(screen.queryByText('Формирование отчёта')).not.toBeInTheDocument();
});

test('maps the join action to the subscribe step', () => {
  const joining: WarmingAccountState = { ...account('a1', 'active'), last_action: 'join' };
  renderWithClient(
    <WarmingBoard warming={[joining]} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  expect(screen.getByText('Подписка на каналы')).toBeInTheDocument();
});

test('shows the real stories step while the engine is watching stories', () => {
  const watching: WarmingAccountState = { ...account('a1', 'active'), last_action: 'stories' };
  renderWithClient(
    <WarmingBoard warming={[watching]} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  expect(screen.getByText('Просмотр сторис')).toBeInTheDocument();
});

test('folds the DM-send action onto its neighbour step (no DM label on the rail)', () => {
  // send_dm runs right after stories, so it folds forward onto the stories step
  // rather than bouncing the rail backward or adding a mostly-dark DM label.
  const dm: WarmingAccountState = { ...account('a1', 'active'), last_action: 'send_dm' };
  renderWithClient(
    <WarmingBoard warming={[dm]} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  expect(screen.getByText('Просмотр сторис')).toBeInTheDocument();
});

test('an errored account shows where the engine last was, not a cycle-count guess', () => {
  const failed: WarmingAccountState = {
    ...account('a1', 'error'),
    health: 'fail',
    last_action: 'read',
  };
  renderWithClient(
    <WarmingBoard warming={[failed]} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  expect(screen.getByText('Чтение ленты постов')).toBeInTheDocument();
});

test('shows a live pause countdown for a sleeping account', () => {
  const nextRunAt = new Date(Date.now() + 90_000).toISOString(); // ~1:30 ahead
  const paused: WarmingAccountState = {
    ...account('79161234567', 'sleeping'),
    next_run_at: nextRunAt,
  };
  renderWithClient(
    <WarmingBoard warming={[paused]} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  expect(screen.getByText('Пауза для естественности')).toBeInTheDocument();
  // The remaining time to next_run_at is shown as mm:ss (RU "ещё M:SS").
  expect(screen.getByText(/ещё 1:\d\d/)).toBeInTheDocument();
});
