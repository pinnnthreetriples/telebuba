import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

const navigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}));

import { WarmConfigModal } from './WarmConfigModal';

test('close just closes the modal', async () => {
  const onClose = vi.fn();
  render(<WarmConfigModal phone="+79991234567" onClose={onClose} />);
  expect(screen.getByText('Настройки прогрева')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Закрыть'));
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(navigate).not.toHaveBeenCalled();
});

test('opening settings closes the modal and navigates to /settings', async () => {
  const onClose = vi.fn();
  render(<WarmConfigModal phone="+79991234567" onClose={onClose} />);

  await userEvent.click(screen.getByText('Открыть настройки'));
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(navigate).toHaveBeenCalledWith({ to: '/settings' });
});
