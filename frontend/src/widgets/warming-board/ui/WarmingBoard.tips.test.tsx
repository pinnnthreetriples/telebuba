import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { WarmingAccountState } from '@/shared/api';

import { WarmingBoard } from './WarmingBoard';

// Its own file rather than more rows in WarmingBoard.test.tsx, which is 670 lines against
// this repo's 700-line cap for a test source.
//
// A warming card carries the app's only two `.tb-tip` triggers that are plain <span>s: the
// daily-actions counter and the "?" beside the cycle count. `.tb-tip-pop` opens on
// `:hover` and `:focus-within` (the stylesheet's half is asserted in
// src/app/styles/index.test.ts — happy-dom applies no CSS, so the reveal itself cannot be
// seen from here), and neither span could take focus, so the second selector could never
// fire and the explanations were pointer-only.

const ACCOUNTS: WarmingAccountState[] = [
  {
    account_id: '79051184490',
    label: '79051184490',
    state: 'active',
    health: 'ok',
    cycles_completed: 2,
    daily_actions: 3,
    daily_cap: 20,
  },
  {
    account_id: '79161234567',
    label: '79161234567',
    state: 'active',
    health: 'ok',
    cycles_completed: 5,
    daily_actions: 7,
    daily_cap: 20,
  },
];

function renderBoard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <WarmingBoard
        warming={ACCOUNTS}
        onStop={vi.fn()}
        onPromote={vi.fn()}
        busyIds={new Set<string>()}
      />
    </QueryClientProvider>,
  );
}

function tipFor(trigger: HTMLElement): HTMLElement | null {
  return document.getElementById(trigger.getAttribute('aria-describedby') ?? '');
}

test('the daily-actions counter takes focus and names its tooltip', () => {
  renderBoard();
  const counter = screen.getByText('3/20');

  counter.focus();
  expect(counter).toHaveFocus();

  const tip = tipFor(counter);
  expect(tip).toHaveAttribute('role', 'tooltip');
  expect(tip).toHaveTextContent(/Действия за сегодня из дневного лимита/);
});

test('the cycle badge takes focus and names its tooltip', () => {
  renderBoard();
  const badge = screen.getAllByText('?')[0];

  badge?.focus();
  expect(badge).toHaveFocus();

  const tip = badge ? tipFor(badge) : null;
  expect(tip).toHaveAttribute('role', 'tooltip');
  expect(tip).toHaveTextContent(/Цикл 2/);
});

// The board renders one card per account, so a literal id would have made every card's
// badge describe the FIRST card's bubble — `aria-describedby` resolves by id and stops at
// the first match. `useId` is what keeps the two cards apart.
test('each card describes its own tooltips, not the first card"s', () => {
  renderBoard();
  const [first, second] = screen.getAllByText('?');

  const firstTip = first ? tipFor(first) : null;
  const secondTip = second ? tipFor(second) : null;
  expect(firstTip).not.toBeNull();
  expect(secondTip).not.toBeNull();
  expect(firstTip).not.toBe(secondTip);
  expect(firstTip).toHaveTextContent(/Цикл 2/);
  expect(secondTip).toHaveTextContent(/Цикл 5/);
});
