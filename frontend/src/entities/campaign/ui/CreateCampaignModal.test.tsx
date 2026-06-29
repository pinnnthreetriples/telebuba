import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { CreateCampaignModal } from './CreateCampaignModal';

test('fills name/prompt, adds and removes channels, creates', async () => {
  const onClose = vi.fn();
  const onCreate = vi.fn();
  render(<CreateCampaignModal onClose={onClose} onCreate={onCreate} />);
  expect(screen.getByText('Новая кампания')).toBeInTheDocument();

  // confirm disabled until name + prompt filled
  const confirm = screen.getByText('Создать кампанию');
  expect(confirm).toBeDisabled();

  await userEvent.type(screen.getByLabelText('Название'), 'Крипто');
  await userEvent.type(screen.getByLabelText('Промт для LLM'), 'пиши дружелюбно');
  expect(confirm).toBeEnabled();

  // add two channels (button + Enter), then remove one
  const channelInput = screen.getByLabelText('t.me/канал или @канал');
  await userEvent.type(channelInput, '@one');
  await userEvent.click(screen.getByText('Добавить'));
  expect(screen.getByText('@one')).toBeInTheDocument();

  await userEvent.type(channelInput, '@two{Enter}');
  expect(screen.getByText('@two')).toBeInTheDocument();

  // blank channel is ignored
  await userEvent.click(screen.getByText('Добавить'));

  await userEvent.click(screen.getAllByLabelText('Убрать канал')[0]!);
  expect(screen.queryByText('@one')).not.toBeInTheDocument();

  await userEvent.click(confirm);
  expect(onCreate).toHaveBeenCalledWith({
    name: 'Крипто',
    prompt: 'пиши дружелюбно',
    channels: ['@two'],
  });
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('cancel closes', async () => {
  const onClose = vi.fn();
  render(<CreateCampaignModal onClose={onClose} onCreate={vi.fn()} />);
  await userEvent.click(screen.getByText('Отмена'));
  expect(onClose).toHaveBeenCalledTimes(1);
});
