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

test('messages: a repeat count lays the round out and is saved', async () => {
  routeApi();
  open();
  const user = userEvent.setup();

  expect(screen.queryByText('Один круг в чате:')).toBeNull();
  await user.click(screen.getByRole('button', { name: 'Отправлять чаще' }));
  await user.click(screen.getByRole('button', { name: 'Отправлять чаще' }));
  expect(screen.getByText('Один круг в чате:')).toBeInTheDocument();
  expect(screen.getByText('1 · 3-й раз')).toBeInTheDocument();
  expect(screen.getByText('4 сообщ. за круг')).toBeInTheDocument();
  // The text is rewritten per send, so its copies differ: no warning yet.
  expect(screen.queryByText(/уйдут в чат одинаковыми/)).toBeNull();

  // A forwarded post is the same post every time.
  await user.click(screen.getByRole('button', { name: /mychannel/ }));
  await user.click(screen.getByRole('button', { name: 'Отправлять чаще' }));
  expect(screen.getByText(/уйдут в чат одинаковыми/)).toBeInTheDocument();
  expect(screen.getByText('5 сообщ. за круг')).toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: 'Сохранить' }));
  const preview = await screen.findByRole('dialog', { name: 'Проверьте, как пойдёт рассылка' });
  await user.click(within(preview).getByRole('button', { name: 'Подтвердить' }));
  await waitFor(() => {
    expect(callsTo(PUT, 'PUT')).toHaveLength(1);
  });
  const body = (await callsTo(PUT, 'PUT')[0]!.clone().json()) as {
    settings: { messages: { repeat: number }[] };
  };
  expect(body.settings.messages.map((message) => message.repeat)).toEqual([3, 2]);
});

// The operator's report: a click on the grey veil around the edited dialog closed it and
// silently threw the edits away.
test('an edited dialog asks before a backdrop click closes it', async () => {
  routeApi();
  const { onClose } = open();
  const user = userEvent.setup();
  const dialog = screen.getByRole('dialog', { name: 'Настройки рассылки' });

  // Untouched: the veil closes it at once.
  await user.click(dialog.parentElement!);
  expect(onClose).toHaveBeenCalledTimes(1);

  await user.click(screen.getByRole('switch', { name: 'Имитация набора' }));
  await user.click(dialog.parentElement!);
  expect(onClose).toHaveBeenCalledTimes(1);
  const question = screen.getByRole('dialog', { name: 'Закрыть без сохранения?' });

  await user.click(within(question).getByRole('button', { name: 'Остаться' }));
  expect(screen.getByText('Не сохранено')).toBeInTheDocument();

  // Cancel in the footer goes through the same question.
  await user.click(screen.getByRole('button', { name: 'Отмена' }));
  await user.click(screen.getByRole('button', { name: 'Закрыть без сохранения' }));
  expect(onClose).toHaveBeenCalledTimes(2);
  expect(callsTo(PUT, 'PUT')).toHaveLength(0);
});

test('links typed in the chat editor but not added count as unsaved input', async () => {
  routeApi();
  const { onClose } = open();
  const user = userEvent.setup();
  const dialog = screen.getByRole('dialog', { name: 'Настройки рассылки' });

  await user.click(screen.getByRole('button', { name: '+ Добавить чаты' }));
  await user.type(screen.getByLabelText('Добавить чаты'), '@beta');
  await user.click(dialog.parentElement!);
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByRole('dialog', { name: 'Закрыть без сохранения?' })).toBeInTheDocument();
});

test('Escape in the chat editor cancels the editor only', async () => {
  routeApi();
  const { onClose } = open();
  const user = userEvent.setup();

  await user.click(screen.getByRole('button', { name: '+ Добавить чаты' }));
  await user.type(screen.getByLabelText('Добавить чаты'), '@beta{Escape}');
  expect(screen.queryByLabelText('Добавить чаты')).toBeNull();
  expect(screen.getByRole('dialog', { name: 'Настройки рассылки' })).toBeInTheDocument();
  expect(screen.queryByRole('dialog', { name: 'Закрыть без сохранения?' })).toBeNull();
  expect(onClose).not.toHaveBeenCalled();
});

test('while the save is in flight every exit is shut, Escape and Cancel included', async () => {
  routeApi();
  const base = vi.mocked(fetch).getMockImplementation()!;
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const request = input as Request;
    if (request.method === 'PUT') await gate;
    return base(input, init);
  });
  const { onClose } = open();
  const user = userEvent.setup();

  await user.click(screen.getByRole('switch', { name: 'Имитация набора' }));
  await user.click(screen.getByRole('button', { name: 'Сохранить' }));
  const preview = await screen.findByRole('dialog', { name: 'Проверьте, как пойдёт рассылка' });
  await user.click(within(preview).getByRole('button', { name: 'Подтвердить' }));
  await waitFor(() => {
    expect(callsTo(PUT, 'PUT')).toHaveLength(1);
  });

  await user.keyboard('{Escape}');
  expect(
    screen.getByRole('dialog', { name: 'Проверьте, как пойдёт рассылка' }),
  ).toBeInTheDocument();
  expect(within(preview).getByRole('button', { name: 'Изменить' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'Отмена' }));
  expect(screen.queryByRole('dialog', { name: 'Закрыть без сохранения?' })).toBeNull();
  expect(onClose).not.toHaveBeenCalled();

  release();
  await waitFor(() => {
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

const SAME = /уйдут в чат одинаковыми/;

function withMessages(
  messages: ChatBroadcastSettingsRead['settings']['messages'],
  extra: Partial<ChatBroadcastSettingsRead['settings']> = {},
): ChatBroadcastSettingsRead {
  return { ...SETTINGS_READ, settings: { ...SETTINGS_READ.settings, ...extra, messages } };
}

const EMPTY = { kind: 'text', text: '', photo: null, post: '' } as const;

test('messages: an empty message that repeats is dropped by the engine, so it warns of nothing', () => {
  routeApi();
  open(
    withMessages([
      { ...EMPTY, repeat: 3 },
      { kind: 'text', text: 'Привет', photo: null, post: '', repeat: 1 },
    ]),
  );

  expect(screen.queryByText('Один круг в чате:')).toBeNull();
  expect(screen.queryByText(SAME)).toBeNull();
});

test('messages: a photo with no text is not rewritten, so its copies are identical', () => {
  routeApi();
  open(
    withMessages(
      [{ ...EMPTY, photo: { media_id: `${'a'.repeat(64)}.png`, name: 'bot.png' }, repeat: 2 }],
      { first_message: 'template', randomize: true },
    ),
  );

  expect(screen.getByText('Один круг в чате:')).toBeInTheDocument();
  expect(screen.getByText(SAME)).toBeInTheDocument();
});

test('messages: the AI varies the first message only when there is a brief to write from', () => {
  routeApi();
  const text = { kind: 'text', text: 'Привет', photo: null, post: '', repeat: 2 } as const;
  const { unmount } = renderPage(
    <SettingsDialog
      read={withMessages([text], { first_message: 'ai', ai_brief: '' })}
      fleet={FLEET as AccountRead[]}
      onClose={vi.fn()}
      onSaved={vi.fn()}
    />,
  );
  expect(screen.getByText(SAME)).toBeInTheDocument();
  unmount();

  open(withMessages([text], { first_message: 'ai', ai_brief: 'Делаем ботов' }));
  expect(screen.getByText('Один круг в чате:')).toBeInTheDocument();
  expect(screen.queryByText(SAME)).toBeNull();
});
