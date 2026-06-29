import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { WarmConfigModal } from './WarmConfigModal';

test('toggles a switch, switches scope tabs and hides the quiet-hours block', async () => {
  render(<WarmConfigModal phone="+79991234567" onClose={vi.fn()} />);
  expect(screen.getByText('Настройки прогрева')).toBeInTheDocument();

  // switches render in order: reactions, join, chat (behaviour), readiness,
  // quietHours (limits). Flipping the first toggles its aria-checked.
  const switches = screen.getAllByRole('switch');
  const reactions = switches[0]!;
  expect(reactions).toHaveAttribute('aria-checked', 'true');
  await userEvent.click(reactions);
  expect(reactions).toHaveAttribute('aria-checked', 'false');

  // quiet-hours starts on → time picker (the hint) is shown, then toggling hides it
  expect(screen.getByText('Часовой пояс берётся из локали аккаунта')).toBeInTheDocument();
  await userEvent.click(switches[4]!);
  expect(screen.queryByText('Часовой пояс берётся из локали аккаунта')).not.toBeInTheDocument();

  // scope tabs
  await userEvent.click(screen.getByText('Все в прогреве'));
  await userEvent.click(screen.getByText('Только этот'));
});

test('save and cancel both close', async () => {
  const onClose = vi.fn();
  render(<WarmConfigModal phone="+79991234567" onClose={onClose} />);
  await userEvent.click(screen.getByText('Сохранить'));
  await userEvent.click(screen.getByText('Отмена'));
  expect(onClose).toHaveBeenCalledTimes(2);
});

test('typing into the quiet-hours time inputs updates them', async () => {
  render(<WarmConfigModal phone="+79991234567" onClose={vi.fn()} />);
  const inputs = screen.getAllByDisplayValue('00');
  await userEvent.clear(inputs[0]!);
  await userEvent.type(inputs[0]!, '30');
  expect(inputs[0]).toHaveValue('30');
});
