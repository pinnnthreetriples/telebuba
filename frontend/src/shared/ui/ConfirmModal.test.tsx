import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { ConfirmModal } from './ConfirmModal';

test('cancel closes without confirming', async () => {
  const onClose = vi.fn();
  const onConfirm = vi.fn();
  render(
    <ConfirmModal
      title="Удалить канал?"
      body="Это действие необратимо."
      confirmLabel="Удалить"
      cancelLabel="Отмена"
      onClose={onClose}
      onConfirm={onConfirm}
    />,
  );
  expect(screen.getByText('Удалить канал?')).toBeInTheDocument();
  await userEvent.click(screen.getByText('Отмена'));
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(onConfirm).not.toHaveBeenCalled();
});

test('confirm calls onConfirm then onClose', async () => {
  const onClose = vi.fn();
  const onConfirm = vi.fn();
  render(
    <ConfirmModal
      title="Удалить канал?"
      body="Это действие необратимо."
      confirmLabel="Удалить"
      cancelLabel="Отмена"
      onClose={onClose}
      onConfirm={onConfirm}
    />,
  );
  await userEvent.click(screen.getByText('Удалить'));
  expect(onConfirm).toHaveBeenCalledTimes(1);
  expect(onClose).toHaveBeenCalledTimes(1);
});
