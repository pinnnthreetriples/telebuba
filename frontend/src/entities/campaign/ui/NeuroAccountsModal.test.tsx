import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { NeuroAccountsModal, type NeuroAccountRow } from './NeuroAccountsModal';

const ACCOUNTS: NeuroAccountRow[] = [
  { account_id: 'a1', phone: '+79990000001', channel: '@crypto' },
  { account_id: 'a2', phone: '+79990000002', channel: null },
];

test('picks a channel, removes an account and closes', async () => {
  const onClose = vi.fn();
  const onPick = vi.fn();
  const onRemove = vi.fn();
  render(
    <NeuroAccountsModal
      accounts={ACCOUNTS}
      channelOptions={['@crypto', '@news']}
      onClose={onClose}
      onPick={onPick}
      onRemove={onRemove}
    />,
  );
  expect(screen.getByText('Аккаунты в нейрокомментинге')).toBeInTheDocument();
  // the unassigned account shows the placeholder label
  expect(screen.getByText('Не назначен')).toBeInTheDocument();

  // open the second (unassigned) account's dropdown and pick a channel —
  // scoped to that row since both rows render an @news option.
  const row = screen.getByText('+79990000002').closest('div') as HTMLElement;
  await userEvent.click(within(row).getByText('Не назначен'));
  await userEvent.click(within(row).getByText('@news'));
  expect(onPick).toHaveBeenCalledWith('a2', '@news');

  // remove the first account
  await userEvent.click(screen.getAllByLabelText('Убрать из нейрокомментинга')[0]!);
  expect(onRemove).toHaveBeenCalledWith('a1');

  await userEvent.click(screen.getByText('Готово'));
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('empty list shows the empty hint', () => {
  render(
    <NeuroAccountsModal
      accounts={[]}
      channelOptions={[]}
      onClose={vi.fn()}
      onPick={vi.fn()}
      onRemove={vi.fn()}
    />,
  );
  expect(screen.getByText('Нет аккаунтов в нейрокомментинге')).toBeInTheDocument();
});
