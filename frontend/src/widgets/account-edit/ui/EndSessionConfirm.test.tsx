import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { EndSessionConfirm } from './EndSessionConfirm';

const ACCOUNT: AccountRead = {
  account_id: 'acc-1',
  status: 'alive',
  devices: ['desktop', 'mobile'],
  second_session_name: 'acc-1_mobile',
  created_at: 'now',
  updated_at: 'now',
};

function renderDialog(account: AccountRead, kind: 'logout' | 'reset' = 'reset') {
  const onConfirm = vi.fn(() => Promise.resolve());
  const onClose = vi.fn();
  render(
    <EndSessionConfirm account={account} kind={kind} onClose={onClose} onConfirm={onConfirm} />,
  );
  return { onConfirm, onClose };
}

test('the dialog names every sign-in that ends, the second device included', () => {
  renderDialog(ACCOUNT);

  expect(screen.getByText('Сбросить сессию?')).toBeVisible();
  expect(screen.getByText('ПК: основная сессия')).toBeVisible();
  expect(screen.getByText('Телефон: второе устройство')).toBeVisible();
  expect(screen.getByText(/а их файлы удалены/)).toBeVisible();
});

test('an alive account ends only after the operator types the word', async () => {
  const { onConfirm } = renderDialog(ACCOUNT, 'logout');
  const confirm = screen.getByRole('button', { name: 'Выйти' });

  expect(confirm).toBeDisabled();
  await userEvent.type(screen.getByLabelText(/введите ЗАВЕРШИТЬ/), 'завершить');
  expect(confirm).toBeEnabled();

  await userEvent.click(confirm);
  await waitFor(() => {
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});

test('an account that is not alive asks without the typed word', async () => {
  const { onConfirm } = renderDialog({ ...ACCOUNT, status: 'unauthorized', devices: ['desktop'] });

  expect(screen.queryByLabelText(/введите ЗАВЕРШИТЬ/)).toBeNull();
  expect(screen.queryByText(/второе устройство/)).toBeNull();
  await userEvent.click(screen.getByRole('button', { name: 'Сбросить' }));
  await waitFor(() => {
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
