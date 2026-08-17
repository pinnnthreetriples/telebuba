import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { NeuroshillingBoardAccount } from '@/shared/api';

import { NeuroshillingAccountsModal } from './NeuroshillingAccountsModal';

const POOL: NeuroshillingBoardAccount[] = [
  { account_id: 'a1', title: 'Алиса', assigned: true },
  { account_id: 'a2', title: 'Борис' },
  { account_id: 'a3', title: 'Виктор', busy_owner: 'warming' },
  {
    account_id: 'a4',
    title: 'Галина',
    busy_owner: 'neuroshilling',
    busy_campaign_name: 'Вторая',
  },
];

test('picks and drops accounts, then saves the whole roster once on «done»', async () => {
  const onSave = vi.fn();
  const onClose = vi.fn();
  render(<NeuroshillingAccountsModal accounts={POOL} onClose={onClose} onSave={onSave} />);

  // The already-rostered account offers "remove"; a free one offers "add".
  const rows = screen.getAllByRole('button', { name: /кампани/ });
  expect(rows[0]).toHaveTextContent('Убрать из кампании');
  expect(rows[1]).toHaveTextContent('Добавить в кампанию');

  await userEvent.click(rows[1]!);
  await userEvent.click(screen.getByText('Готово'));

  // ONE save carrying the final roster — not one request per click.
  expect(onSave).toHaveBeenCalledTimes(1);
  expect(onSave.mock.calls[0]![0]).toEqual(['a1', 'a2']);
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('an account another feature holds is disabled and says who holds it', () => {
  render(<NeuroshillingAccountsModal accounts={POOL} onClose={vi.fn()} onSave={vi.fn()} />);

  const held = screen.getAllByRole('button', { name: 'Добавить в кампанию' });
  // Борис is free, Виктор and Галина are held.
  expect(held[0]).toBeEnabled();
  expect(held[1]).toBeDisabled();
  expect(held[2]).toBeDisabled();

  expect(screen.getByText('занят прогревом')).toBeInTheDocument();
  expect(screen.getByText('занят другой кампанией нейрошиллинга — Вторая')).toBeInTheDocument();
});

test('leaving without «done» writes nothing, so a wrong click can be taken back', async () => {
  const onSave = vi.fn();
  const onClose = vi.fn();
  render(
    // Held elsewhere AND on this roster: dropping it greys the row out, and this
    // dialog offers no way to pick it up again — leaving is the whole undo.
    <NeuroshillingAccountsModal
      accounts={[{ account_id: 'a1', title: 'Алиса', assigned: true, busy_owner: 'warming' }]}
      onClose={onClose}
      onSave={onSave}
    />,
  );
  await userEvent.click(screen.getByRole('button', { name: 'Убрать из кампании' }));

  await userEvent.click(screen.getByText('Отмена'));

  // The roster is replaced whole by the save, so an exit that wrote would turn a
  // look at the list — or one stray click — into the loss of the campaign's cast.
  expect(onSave).not.toHaveBeenCalled();
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('escape leaves the picker without writing the draft', async () => {
  const onSave = vi.fn();
  const onClose = vi.fn();
  render(<NeuroshillingAccountsModal accounts={POOL} onClose={onClose} onSave={onSave} />);
  await userEvent.click(screen.getAllByRole('button', { name: 'Добавить в кампанию' })[0]!);

  await userEvent.keyboard('{Escape}');

  expect(onSave).not.toHaveBeenCalled();
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('dropping an account that is already on the roster is always allowed', async () => {
  const onSave = vi.fn();
  render(
    <NeuroshillingAccountsModal
      // Held elsewhere AND on this roster: the hold must not trap it here.
      accounts={[{ account_id: 'a1', title: 'Алиса', assigned: true, busy_owner: 'warming' }]}
      onClose={vi.fn()}
      onSave={onSave}
    />,
  );

  const drop = screen.getByRole('button', { name: 'Убрать из кампании' });
  expect(drop).toBeEnabled();
  await userEvent.click(drop);
  await userEvent.click(screen.getByText('Готово'));
  expect(onSave).toHaveBeenCalledWith([]);
});

test('an empty pool explains itself', () => {
  render(<NeuroshillingAccountsModal accounts={[]} onClose={vi.fn()} onSave={vi.fn()} />);

  expect(screen.getByText(/Аккаунтов пока нет/)).toBeInTheDocument();
});
