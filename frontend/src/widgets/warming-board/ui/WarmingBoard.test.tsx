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

test('renders an in-progress card per warming account with the stage labels', () => {
  renderWithClient(
    <WarmingBoard warming={WARMING} onStop={vi.fn()} onPromote={vi.fn()} busyId={null} />,
  );
  expect(screen.getByText('79051184490')).toBeInTheDocument();
  expect(screen.getByText('79161234567')).toBeInTheDocument();
  expect(screen.getAllByText('Подписка').length).toBeGreaterThan(0);
  expect(screen.getAllByText('Отчёт').length).toBeGreaterThan(0);
});

test('stops the clicked account', async () => {
  const onStop = vi.fn();
  renderWithClient(
    <WarmingBoard warming={WARMING} onStop={onStop} onPromote={vi.fn()} busyId={null} />,
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
    <WarmingBoard warming={[done]} onStop={vi.fn()} onPromote={onPromote} busyId={null} />,
  );
  await userEvent.click(screen.getByText('Отправить в прогретые'));
  expect(onPromote).toHaveBeenCalledWith('79051184490');
});

test('keeps an account in progress below its target', () => {
  renderWithClient(
    <WarmingBoard
      warming={[warmed('79051184490', 2, 7)]}
      onStop={vi.fn()}
      onPromote={vi.fn()}
      busyId={null}
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
      busyId={null}
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
        busyId={null}
        feedback={{ '79051184490': 'err' }}
      />
    </QueryClientProvider>,
  );
  expect(document.querySelector('.text-danger svg')).toBeInTheDocument();
});

test('renders the phone as the card id when present', () => {
  const withPhone: WarmingAccountState = {
    ...account('a1', 'active'),
    label: 'Label A',
    phone: '+79215532011',
  };
  renderWithClient(
    <WarmingBoard warming={[withPhone]} onStop={vi.fn()} onPromote={vi.fn()} busyId={null} />,
  );
  expect(screen.getByText('+79215532011')).toBeInTheDocument();
  expect(screen.queryByText('Label A')).not.toBeInTheDocument();
});

test('the "?" tooltip shows the ЛС (DM) line from dm_allowed', () => {
  const allowed: WarmingAccountState = { ...account('a1', 'active'), dm_allowed: true };
  const closed: WarmingAccountState = { ...account('a2', 'active'), dm_allowed: false };
  renderWithClient(
    <WarmingBoard warming={[allowed, closed]} onStop={vi.fn()} onPromote={vi.fn()} busyId={null} />,
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
    <WarmingBoard warming={[acc]} onStop={vi.fn()} onPromote={vi.fn()} busyId={null} />,
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
      busyId={null}
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
    <WarmingBoard warming={WARMING} onStop={vi.fn()} onPromote={vi.fn()} busyId={null} />,
  );
  await userEvent.click(screen.getAllByText('Лог активности')[0]!);
  await waitFor(() => {
    expect(screen.getByText('warming_subscribe')).toBeInTheDocument();
  });
  const fetched = vi
    .mocked(fetch)
    .mock.calls.some(([input]) => (input as Request).url.includes('account_id=79051184490'));
  expect(fetched).toBe(true);
});
