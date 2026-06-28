import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { WarmingAccountState } from '@/shared/api';

import { WarmingBoard } from './WarmingBoard';

function account(id: string, state: WarmingAccountState['state']): WarmingAccountState {
  return { account_id: id, label: id, state, health: 'ok', cycles_completed: 2, trust_score: 70 };
}

const WARMING = [account('79051184490', 'active'), account('79161234567', 'sleeping')];

test('renders an in-progress card per warming account with the stage labels', () => {
  render(<WarmingBoard warming={WARMING} onStop={vi.fn()} busyId={null} />);
  expect(screen.getByText('79051184490')).toBeInTheDocument();
  expect(screen.getByText('79161234567')).toBeInTheDocument();
  expect(screen.getAllByText('Подписка').length).toBeGreaterThan(0);
  expect(screen.getAllByText('Отчёт').length).toBeGreaterThan(0);
});

test('stops the clicked account', async () => {
  const onStop = vi.fn();
  render(<WarmingBoard warming={WARMING} onStop={onStop} busyId={null} />);
  await userEvent.click(screen.getAllByText('Остановить')[0]!);
  expect(onStop).toHaveBeenCalledWith('79051184490');
});
