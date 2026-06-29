import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { DeleteAccountModal } from './DeleteAccountModal';

test('confirm fires onConfirm then onClose, cancel only closes', async () => {
  const onClose = vi.fn();
  const onConfirm = vi.fn();
  const { rerender } = render(
    <DeleteAccountModal phone="+79991234567" onClose={onClose} onConfirm={onConfirm} />,
  );
  expect(screen.getByText('Удалить аккаунт +79991234567?')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Удалить'));
  expect(onConfirm).toHaveBeenCalledTimes(1);
  expect(onClose).toHaveBeenCalledTimes(1);

  rerender(<DeleteAccountModal phone="+79991234567" onClose={onClose} onConfirm={onConfirm} />);
  await userEvent.click(screen.getByText('Отмена'));
  expect(onClose).toHaveBeenCalledTimes(2);
  expect(onConfirm).toHaveBeenCalledTimes(1);
});
