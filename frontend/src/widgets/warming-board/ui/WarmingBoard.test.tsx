import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { WarmingAccountState } from '@/shared/api';

import { WarmingBoard } from './WarmingBoard';

function account(id: string, state: WarmingAccountState['state']): WarmingAccountState {
  return { account_id: id, label: id, state, health: 'ok', cycles_completed: 0 };
}

test('renders idle and warming columns and fires start/stop', async () => {
  const onStart = vi.fn();
  const onStop = vi.fn();
  render(
    <WarmingBoard
      idle={[account('idle-1', 'idle')]}
      warming={[account('warm-1', 'active')]}
      onStart={onStart}
      onStop={onStop}
      busyId={null}
    />,
  );

  expect(screen.getByText('idle-1')).toBeInTheDocument();
  expect(screen.getByText('warm-1')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Запустить'));
  await userEvent.click(screen.getByText('Остановить'));
  expect(onStart).toHaveBeenCalledWith('idle-1');
  expect(onStop).toHaveBeenCalledWith('warm-1');
});
