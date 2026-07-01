import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { WarmDaysModal } from './WarmDaysModal';

test('presets, keyboard arrows and confirm', async () => {
  const onClose = vi.fn();
  const onConfirm = vi.fn();
  render(<WarmDaysModal phone="+79991234567" onClose={onClose} onConfirm={onConfirm} />);
  expect(screen.getByText('Прогрев аккаунта')).toBeInTheDocument();

  const slider = screen.getByRole('slider');
  expect(slider).toHaveAttribute('aria-valuenow', '7');

  // preset button sets the day count
  await userEvent.click(screen.getByText('3 дня'));
  expect(slider).toHaveAttribute('aria-valuenow', '3');

  // keyboard arrows move within bounds
  slider.focus();
  await userEvent.keyboard('{ArrowRight}{ArrowRight}');
  expect(slider).toHaveAttribute('aria-valuenow', '5');
  await userEvent.keyboard('{ArrowLeft}');
  expect(slider).toHaveAttribute('aria-valuenow', '4');

  await userEvent.click(screen.getByText('Запустить прогрев'));
  // defaults to the balanced persona when none is picked
  expect(onConfirm).toHaveBeenCalledWith(4, 'normal');
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('persona chip selection is forwarded on confirm', async () => {
  const onClose = vi.fn();
  const onConfirm = vi.fn();
  render(<WarmDaysModal phone="+79991234567" onClose={onClose} onConfirm={onConfirm} />);

  await userEvent.click(screen.getByText('Активный'));
  await userEvent.click(screen.getByText('Запустить прогрев'));

  expect(onConfirm).toHaveBeenCalledWith(7, 'active');
});

test('cancel closes without confirming', async () => {
  const onClose = vi.fn();
  const onConfirm = vi.fn();
  render(<WarmDaysModal phone="+79991234567" onClose={onClose} onConfirm={onConfirm} />);
  await userEvent.click(screen.getByText('Отмена'));
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(onConfirm).not.toHaveBeenCalled();
});
