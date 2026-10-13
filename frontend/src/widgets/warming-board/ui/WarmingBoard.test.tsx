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
    warmed_days: days,
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

test('renders one card per warming account without the old stage stepper', () => {
  renderWithClient(
    <WarmingBoard warming={WARMING} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  expect(screen.getByText('79051184490')).toBeInTheDocument();
  expect(screen.getByText('79161234567')).toBeInTheDocument();
  // Item 2.3 removed the five-stage rail and the cycle-number hint.
  expect(screen.queryByText('Подписка')).not.toBeInTheDocument();
  expect(screen.queryByText('Сторис')).not.toBeInTheDocument();
  expect(screen.queryByText('?')).not.toBeInTheDocument();
});

test('the phase row names the phase, persona, trust and spam status', () => {
  const acc: WarmingAccountState = {
    ...account('a1', 'active'),
    phase: 'settling',
    progress_to_next: 0.4,
    days_to_next_phase: 3,
    activity_persona: 'calm',
    spam_status: 'limited',
  };
  renderWithClient(
    <WarmingBoard warming={[acc]} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  expect(screen.getAllByText('Фаза: обживается').length).toBeGreaterThan(0);
  expect(screen.getByText('до следующей фазы: 3 дн.')).toBeInTheDocument();
  expect(screen.getByText('Спокойный')).toBeInTheDocument();
  expect(screen.getByText('Доверие 70')).toHaveClass('text-success-deep');
  expect(screen.getByText('Спам: ограничен')).toBeInTheDocument();
  expect(screen.getByRole('progressbar', { name: 'Фаза: обживается' })).toBeInTheDocument();
});

test('an unchecked spam status says so', () => {
  renderWithClient(
    <WarmingBoard
      warming={[account('a1', 'active')]}
      onStop={vi.fn()}
      onPromote={vi.fn()}
      busyIds={NONE_BUSY}
    />,
  );
  expect(screen.getByText('Спам: не проверялся')).toBeInTheDocument();
});

test('today counts settled actions, not the running booking, with the DM status', () => {
  const acc: WarmingAccountState = {
    ...account('a1', 'active'),
    daily_actions: 15,
    daily_settled: 6,
    daily_cap: 15,
    dm_status: 'chat_off',
  };
  renderWithClient(
    <WarmingBoard warming={[acc]} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  expect(
    screen.getByText('Сегодня 6 из 15 действий · ЛС: выкл., переписка отключена'),
  ).toBeInTheDocument();
  expect(screen.queryByText(/15 из 15/)).not.toBeInTheDocument();
});

test('a resting account shows when it comes back, and open DMs', () => {
  const resting: WarmingAccountState = {
    ...account('a1', 'sleeping'),
    daily_settled: 4,
    daily_cap: 0,
    dm_status: 'on',
    next_run_at: new Date(Date.now() + 80 * 60_000).toISOString(),
  };
  renderWithClient(
    <WarmingBoard warming={[resting]} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  expect(
    screen.getByText('Сегодня 4 действий · следующий заход через 1:20 · ЛС: вкл.'),
  ).toBeInTheDocument();
});

test('the problem row appears only when there is a problem', () => {
  const quarantined: WarmingAccountState = {
    ...account('q', 'quarantine'),
    attention: { code: 'quarantine', attempt: 2, attempts: 3 },
  };
  const unready: WarmingAccountState = {
    ...account('n', 'active'),
    attention: { code: 'not_ready', reasons: ['no proxy', 'mystery'] },
  };
  const flooded: WarmingAccountState = {
    ...account('f', 'flood_wait'),
    attention: { code: 'flood_wait', until: '2026-06-13T14:30:00Z' },
  };
  const stopping: WarmingAccountState = {
    ...account('s', 'error'),
    attention: { code: 'stopping' },
  };
  renderWithClient(
    <WarmingBoard
      warming={[quarantined, unready, flooded, stopping, account('ok', 'active')]}
      onStop={vi.fn()}
      onPromote={vi.fn()}
      busyIds={NONE_BUSY}
    />,
  );
  expect(screen.getByText('Карантин 2/3')).toBeInTheDocument();
  expect(screen.getByText('Не готов: нет прокси, mystery')).toBeInTheDocument();
  expect(screen.getByText(/^Флуд до \d\d:\d\d$/)).toBeInTheDocument();
  expect(screen.getByText('Останавливается…')).toBeInTheDocument();
  expect(screen.getAllByRole('status')).toHaveLength(4);
});

test('a quarantine with a re-check time names it', () => {
  const quarantined: WarmingAccountState = {
    ...account('q', 'quarantine'),
    attention: { code: 'quarantine', attempt: 1, attempts: 3, until: '2026-06-13T14:30:00Z' },
  };
  renderWithClient(
    <WarmingBoard
      warming={[quarantined]}
      onStop={vi.fn()}
      onPromote={vi.fn()}
      busyIds={NONE_BUSY}
    />,
  );
  expect(screen.getByText(/^Карантин 1\/3 · до \d\d:\d\d$/)).toBeInTheDocument();
});

test('an accepted geo mismatch shows its grey badge', () => {
  const accepted: WarmingAccountState = {
    ...account('a1', 'active'),
    geo_status: 'mismatch',
    geo_accepted: true,
  };
  renderWithClient(
    <WarmingBoard warming={[accepted]} onStop={vi.fn()} onPromote={vi.fn()} busyIds={NONE_BUSY} />,
  );
  expect(screen.getByText('Гео ≠ (принято)')).toBeInTheDocument();
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
  // 3 worked days against a chosen target of 3 → the card flips to complete
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
  expect(document.querySelector('.text-success-deep svg')).toBeInTheDocument();

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
    expect(screen.getByText('· ограничение по спаму')).toBeInTheDocument();
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
