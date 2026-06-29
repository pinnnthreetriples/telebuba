import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { ProxyAddModal } from './ProxyAddModal';

test('renders the shared form and closes from add/cancel/close', async () => {
  const onClose = vi.fn();
  render(<ProxyAddModal onClose={onClose} />);
  expect(screen.getByText('Добавить прокси')).toBeInTheDocument();
  // shared ProxyForm fields are present
  expect(screen.getByText('Хост')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Добавить'));
  await userEvent.click(screen.getByText('Отмена'));
  await userEvent.click(screen.getByLabelText('Закрыть'));
  expect(onClose).toHaveBeenCalledTimes(3);
});
