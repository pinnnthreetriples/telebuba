import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { SpamRetryModal } from './SpamRetryModal';

test('offers only a retry, and stays open while @SpamBot still does not answer', async () => {
  const onClose = vi.fn();
  const onRetry = vi.fn().mockRejectedValueOnce(new Error('no answer')).mockResolvedValueOnce(null);
  render(<SpamRetryModal onClose={onClose} onRetry={onRetry} />);

  expect(screen.getByRole('dialog', { name: '@SpamBot не ответил' })).toBeInTheDocument();
  expect(screen.queryByText(/без проверки/i)).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  expect(onRetry).toHaveBeenCalledTimes(1);
  expect(onClose).not.toHaveBeenCalled();

  await userEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  expect(onRetry).toHaveBeenCalledTimes(2);
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('close dismisses without retrying', async () => {
  const onClose = vi.fn();
  const onRetry = vi.fn();
  render(<SpamRetryModal onClose={onClose} onRetry={onRetry} />);

  await userEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
  expect(onClose).toHaveBeenCalled();
  expect(onRetry).not.toHaveBeenCalled();
});
