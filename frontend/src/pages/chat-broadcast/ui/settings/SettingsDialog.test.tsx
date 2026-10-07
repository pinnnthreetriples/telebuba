import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import type { AccountRead, ChatBroadcastSettingsRead } from '@/shared/api';

import { SETTINGS_READ } from '../../model/fixtures.test-helpers';
import { callsTo, FLEET, renderPage, routeApi } from '../ChatBroadcastPage.testHelpers';

import { SettingsDialog } from './SettingsDialog';

const PUT = '/api/v1/chat-broadcast/campaigns/c1/settings';

function open(read: ChatBroadcastSettingsRead = SETTINGS_READ) {
  const onClose = vi.fn();
  const onSaved = vi.fn();
  renderPage(
    <SettingsDialog
      read={read}
      fleet={FLEET as AccountRead[]}
      onClose={onClose}
      onSaved={onSaved}
    />,
  );
  return { onClose, onSaved };
}

test('editing marks the dialog unsaved and Confirm writes the draft', async () => {
  routeApi();
  const { onClose, onSaved } = open();
  const user = userEvent.setup();

  expect(screen.queryByText('Не сохранено')).toBeNull();
  await user.click(screen.getByRole('switch', { name: 'Имитация набора' }));
  expect(screen.getByText('Не сохранено')).toBeInTheDocument();
  await user.click(screen.getByRole('radio', { name: '2 часа' }));

  await user.click(screen.getByRole('button', { name: 'Сохранить' }));
  const preview = await screen.findByRole('dialog', { name: 'Проверьте, как пойдёт рассылка' });
  await user.click(within(preview).getByRole('button', { name: 'Подтвердить' }));

  await waitFor(() => {
    expect(callsTo(PUT, 'PUT')).toHaveLength(1);
  });
  const body = (await callsTo(PUT, 'PUT')[0]!.clone().json()) as {
    expected_updated_at: string;
    settings: { typing: boolean; join_delay_minutes: number };
  };
  expect(body.expected_updated_at).toBe(SETTINGS_READ.updated_at);
  expect(body.settings).toMatchObject({ typing: false, join_delay_minutes: 120 });
  await waitFor(() => {
    expect(onSaved).toHaveBeenCalled();
  });
  expect(onClose).toHaveBeenCalled();
});

test('the check window blocks Confirm while nothing could run', async () => {
  routeApi();
  open({ ...SETTINGS_READ, account_ids: [], settings: { targets: [], messages: [] } });
  const user = userEvent.setup();

  await user.click(screen.getByRole('button', { name: 'Сохранить' }));

  const preview = await screen.findByRole('dialog', { name: 'Проверьте, как пойдёт рассылка' });
  expect(within(preview).getByText('Не выбраны аккаунты.')).toBeInTheDocument();
  expect(within(preview).getByText('Нет ни одного чата.')).toBeInTheDocument();
  expect(within(preview).getByRole('button', { name: 'Подтвердить' })).toBeDisabled();
  await user.click(within(preview).getByRole('button', { name: 'Изменить' }));
  expect(screen.queryByRole('dialog', { name: 'Проверьте, как пойдёт рассылка' })).toBeNull();
});

test('a save that lost the race says so and blocks another one', async () => {
  const api = routeApi();
  api.refuse = { path: '/settings', status: 409, code: 'campaign_changed' };
  open();
  const user = userEvent.setup();

  await user.click(screen.getByRole('button', { name: 'Сохранить' }));
  const preview = await screen.findByRole('dialog', { name: 'Проверьте, как пойдёт рассылка' });
  await user.click(within(preview).getByRole('button', { name: 'Подтвердить' }));

  expect(await screen.findByRole('alert')).toHaveTextContent('успели измениться');
  expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
});

test('chats: pasted links are checked, folders counted, junk refused', async () => {
  routeApi();
  open();
  const user = userEvent.setup();

  await user.click(screen.getByRole('button', { name: '+ Добавить чаты' }));
  await user.type(screen.getByLabelText('Добавить чаты'), '@beta t.me/addlist/abc !!bad');
  await user.click(screen.getByRole('button', { name: 'Добавить' }));

  expect(await screen.findByText('@beta')).toBeInTheDocument();
  expect(await screen.findByText('папка · 9 чатов')).toBeInTheDocument();
  expect(screen.getByText('Не распознаны: !!bad')).toBeInTheDocument();
  expect(screen.getByText('закрытый чат')).toBeInTheDocument();
  await user.click(screen.getByLabelText('Убрать @beta'));
  expect(screen.queryByText('@beta')).toBeNull();
});

test('chats: "where it already is" lists the groups and crosses one out', async () => {
  routeApi();
  open();
  const user = userEvent.setup();

  await user.click(screen.getByRole('radio', { name: 'Где уже состоит' }));

  expect(await screen.findByText('Общая')).toBeInTheDocument();
  expect(screen.getByText(/пропущено каналов 3/)).toBeInTheDocument();
  await user.click(screen.getByLabelText('Исключить Общая'));
  expect(screen.getByLabelText('Вернуть Общая')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Обновить' }));
});

test('messages: add, switch kind, write a post link, delete', async () => {
  routeApi();
  open();
  const user = userEvent.setup();

  await user.click(screen.getByRole('button', { name: '+ Добавить сообщение' }));
  expect(screen.getByText('3 в цепочке')).toBeInTheDocument();
  const kinds = screen.getAllByRole('radiogroup', { name: 'Что отправить' });
  await user.click(within(kinds.at(-1)!).getByRole('radio', { name: 'Пост' }));
  await user.type(screen.getByLabelText('Ссылка на пост'), 't.me/chan/5');
  const deletes = screen.getAllByLabelText('Удалить сообщение');
  await user.click(deletes.at(-1)!);
  expect(screen.getByText('2 в цепочке')).toBeInTheDocument();
});

test('messages: the AI mode asks for a brief, randomizing tells what happens', async () => {
  routeApi();
  open();
  const user = userEvent.setup();

  expect(screen.getAllByText(/ИИ перепишет перед каждой отправкой/).length).toBeGreaterThan(0);
  await user.click(screen.getByRole('switch', { name: 'Рандомизировать текст' }));
  expect(screen.getByText(/Уйдёт как написано/)).toBeInTheDocument();
  await user.click(screen.getByRole('radio', { name: 'ИИ под чат' }));
  await user.type(screen.getByLabelText('Задание для ИИ'), 'Делаем ботов');
  expect(screen.getByLabelText('Задание для ИИ')).toHaveValue('Делаем ботов');
});

test('messages: a photo is uploaded and shown on the card', async () => {
  routeApi();
  open();
  const user = userEvent.setup();
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;

  await user.upload(input, new File(['png'], 'bot.png', { type: 'image/png' }));

  expect(await screen.findByText('bot.png')).toBeInTheDocument();
  expect(callsTo('/api/v1/chat-broadcast/media', 'POST')).toHaveLength(1);
  await user.click(screen.getByLabelText('Убрать фото'));
  expect(screen.queryByText('bot.png')).toBeNull();
});

test('pace: ranges, volume and safety write the draft', async () => {
  routeApi();
  open();
  const user = userEvent.setup();

  const from = screen.getByLabelText('Между чатами: от');
  await user.clear(from);
  await user.type(from, '100');
  expect(screen.getByLabelText('Между чатами: до')).toHaveValue(100);
  await user.click(screen.getByRole('radio', { name: 'По времени' }));
  expect(screen.getByLabelText('Часов')).toHaveValue(6);
  await user.click(screen.getByRole('switch', { name: 'Повторять по кругу' }));
  expect(screen.queryByLabelText('Кругов')).toBeNull();
  await user.click(screen.getByRole('switch', { name: 'Лимит на аккаунт' }));
  expect(screen.queryByLabelText('в час')).toBeNull();
  await user.click(screen.getByRole('switch', { name: 'Пропускать ошибки' }));
  expect(screen.getByText('Не сохранено')).toBeInTheDocument();
});
