import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import type { WarmingAccountState } from '@/shared/api';

import { PhaseTrack } from './PhaseTrack';
import { TodayBlock } from './TodayBlock';
import { VisitSteps } from './VisitSteps';

function account(over: Partial<WarmingAccountState> = {}): WarmingAccountState {
  return { account_id: 'a1', label: 'a1', state: 'active', health: 'ok', ...over };
}

const PLAN: NonNullable<WarmingAccountState['cycle_plan']> = [
  'set_online',
  'stories',
  'join',
  'read',
  'react',
];

test('today shows one column per kind of action, with its count', () => {
  render(
    <TodayBlock
      account={account({
        state: 'sleeping',
        daily_settled: 16,
        daily_cap: 18,
        today: { reads: 9, reactions: 4, joins: 1, dms: 2 },
      })}
      days={6}
      target={15}
      idle={0}
    />,
  );
  for (const [label, count] of [
    ['Чтение', 9],
    ['Реакции', 4],
    ['Вступления', 1],
    ['Личные сообщения', 2],
  ] as const) {
    expect(screen.getByTitle(`${label}: ${String(count)}`)).toBeInTheDocument();
  }
  expect(screen.getByRole('img', { name: '6 / 15 дней' })).toBeInTheDocument();
});

test('a day with no totals yet shows empty columns, not a gap', () => {
  render(
    <TodayBlock
      account={account({ state: 'sleeping', daily_cap: 15 })}
      days={0}
      target={15}
      idle={0}
    />,
  );
  expect(screen.getByTitle('Чтение: 0')).toBeInTheDocument();
  expect(screen.getByTitle('Личные сообщения: 0')).toBeInTheDocument();
});

test('the live visit card wears the colour and words of the step it is on', () => {
  const { rerender } = render(
    <VisitSteps
      account={account({
        visit_kind: 'binge',
        cycle_plan: PLAN,
        last_action: 'join',
        last_channel: '@news',
      })}
    />,
  );
  let card = screen.getByRole('status');
  expect(card).toHaveAttribute('data-tone', 'join');
  expect(screen.getByText('Вступает в канал')).toBeInTheDocument();
  expect(screen.getByText('Заход: залип · @news')).toBeInTheDocument();
  expect(screen.getByText('было: истории')).toBeInTheDocument();
  expect(screen.getByText('дальше: чтение')).toBeInTheDocument();
  expect(screen.getByText('шаг 3 из 5')).toBeInTheDocument();

  rerender(
    <VisitSteps
      account={account({ visit_kind: 'binge', cycle_plan: PLAN, last_action: 'react' })}
    />,
  );
  card = screen.getByRole('status');
  expect(card).toHaveAttribute('data-tone', 'react');
  expect(screen.getByText('Ставит реакции')).toBeInTheDocument();
  // The last step has nothing after it.
  expect(screen.queryByText(/^дальше:/)).not.toBeInTheDocument();
});

test('no live card while the account is not in a visit', () => {
  const { container } = render(
    <VisitSteps account={account({ state: 'sleeping', visit_kind: 'normal', cycle_plan: PLAN })} />,
  );
  expect(container).toBeEmptyDOMElement();
});

test('the path names the phase, its place and the next one; the last phase has no next', () => {
  const { rerender } = render(
    <PhaseTrack
      account={account({ phase: 'warming', progress_to_next: 0.5, days_to_next_phase: 2 })}
    />,
  );
  expect(screen.getByText('Прогрев')).toBeInTheDocument();
  expect(screen.getByText('Фаза 3 из 5')).toBeInTheDocument();
  expect(screen.getByText('дальше')).toBeInTheDocument();

  rerender(<PhaseTrack account={account({ phase: 'warmed' })} />);
  expect(screen.getByText('Прогрет')).toBeInTheDocument();
  expect(screen.queryByText('дальше')).not.toBeInTheDocument();
});
