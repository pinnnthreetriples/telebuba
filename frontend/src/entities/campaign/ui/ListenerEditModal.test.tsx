import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { ListenerEditModal } from './ListenerEditModal';

const OPTIONS = [
  { id: 'a1', phone: '+79990000001' },
  { id: 'a2', phone: '+79990000002' },
];

test('opens the dropdown, picks an option, saves with swap and closes', async () => {
  const onClose = vi.fn();
  const onSave = vi.fn();
  render(<ListenerEditModal options={OPTIONS} selected={null} onClose={onClose} onSave={onSave} />);
  expect(screen.getByText('Аккаунт-слушатель')).toBeInTheDocument();

  // open the custom dropdown and pick the second option
  await userEvent.click(screen.getByText('Выберите аккаунт…'));
  await userEvent.click(screen.getByText('+79990000002'));

  await userEvent.click(screen.getByText('Сохранить'));
  expect(onSave).toHaveBeenCalledWith('a2');
  expect(screen.getByText('Сохранено')).toBeInTheDocument();
  await waitFor(() => {
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

test('cancel closes without saving', async () => {
  const onClose = vi.fn();
  const onSave = vi.fn();
  render(<ListenerEditModal options={OPTIONS} selected="a1" onClose={onClose} onSave={onSave} />);
  await userEvent.click(screen.getByLabelText('Закрыть'));
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(onSave).not.toHaveBeenCalled();
});
