import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { AddStoryModal } from './AddStoryModal';

test('audience segments, no-forward checkbox, caption typing and close', async () => {
  const onClose = vi.fn();
  render(<AddStoryModal onClose={onClose} />);
  expect(screen.getByText('Новая сторис')).toBeInTheDocument();

  // audience segmented control
  await userEvent.click(screen.getByText('Близкие друзья'));
  await userEvent.click(screen.getByText('Публично'));
  await userEvent.click(screen.getByText('Контакты'));

  // no-forward checkbox toggles (button row text stays, toggling its check state)
  const noForward = screen.getByText('Запретить пересылку сторис');
  await userEvent.click(noForward);
  await userEvent.click(noForward);

  // caption typing
  const caption = screen.getByPlaceholderText('Введите подпись…');
  await userEvent.type(caption, 'привет');
  expect(caption).toHaveValue('привет');

  await userEvent.click(screen.getByText('Опубликовать'));
  await userEvent.click(screen.getByText('Отмена'));
  await userEvent.click(screen.getByLabelText('Закрыть'));
  expect(onClose).toHaveBeenCalledTimes(3);
});
