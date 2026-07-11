import { render, screen, waitFor } from '@testing-library/react';
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

test('an async confirm disables the button and closes only when it resolves', async () => {
  const onClose = vi.fn();
  let resolve!: () => void;
  const onConfirm = vi.fn(
    () =>
      new Promise<void>((res) => {
        resolve = res;
      }),
  );
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
  await userEvent.click(screen.getByRole('button', { name: 'Удалить' }));
  // Pending: the dialog stays open with a disabled confirm button.
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Удалить' })).toBeDisabled();
  resolve();
  await waitFor(() => {
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

test('an async confirm that rejects keeps the dialog open and re-enables the button', async () => {
  const onClose = vi.fn();
  const onConfirm = vi.fn(() => Promise.reject(new Error('boom')));
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
  await userEvent.click(screen.getByRole('button', { name: 'Удалить' }));
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Удалить' })).toBeEnabled();
  });
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByText('Удалить канал?')).toBeInTheDocument();
});
