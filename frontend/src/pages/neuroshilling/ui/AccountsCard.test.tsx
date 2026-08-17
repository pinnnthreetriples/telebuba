import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { AccountsCard } from './AccountsCard';

test('shows the picked accounts, their count and the way into the picker', async () => {
  const onPick = vi.fn();
  render(
    <AccountsCard
      accounts={[
        { account_id: 'a1', title: 'Алиса', assigned: true },
        { account_id: 'a2', title: 'Борис', assigned: true },
      ]}
      onPick={onPick}
    />,
  );

  expect(screen.getByText('Выбрано: 2')).toBeInTheDocument();
  expect(screen.getByText('Алиса')).toBeInTheDocument();
  expect(screen.getByText('Борис')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Выбрать аккаунты'));
  expect(onPick).toHaveBeenCalledTimes(1);
});

test('an empty roster says so instead of showing an empty chip row', () => {
  render(<AccountsCard accounts={[]} onPick={vi.fn()} />);

  expect(screen.getByText('Выбрано: 0')).toBeInTheDocument();
  expect(screen.getByText('Аккаунты пока не выбраны')).toBeInTheDocument();
  // The rule the picker enforces is explained here rather than only on refusal.
  expect(screen.getByRole('note', { name: /минимум два/ })).toBeInTheDocument();
});
