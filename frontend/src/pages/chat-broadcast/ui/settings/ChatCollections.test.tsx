import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import type { AccountRead, ChatCollection } from '@/shared/api';

import { SETTINGS_READ } from '../../model/fixtures.test-helpers';
import { callsTo, FLEET, renderPage, routeApi } from '../ChatBroadcastPage.testHelpers';

import { SettingsDialog } from './SettingsDialog';

const COLLECTIONS = '/api/v1/chat-broadcast/collections';
const CRYPTO: ChatCollection = {
  collection_id: 'k1',
  name: 'Крипта',
  targets: ['t.me/Alpha', 't.me/gamma', '@delta'],
  updated_at: '2026-10-07T09:00:00+00:00',
};

function open() {
  renderPage(
    <SettingsDialog
      read={SETTINGS_READ}
      fleet={FLEET as AccountRead[]}
      onClose={vi.fn()}
      onSaved={vi.fn()}
    />,
  );
}

test('a category adds its chats to the list once, without duplicates', async () => {
  routeApi({ collections: [CRYPTO] });
  open();
  const user = userEvent.setup();

  // «@alpha» is already in the campaign; t.me/Alpha is the same chat.
  const add = await screen.findByRole('button', { name: 'Добавить в рассылку: Крипта' });
  expect(within(add).getByText('3')).toBeInTheDocument();
  await user.click(add);

  expect(screen.getByText('t.me/gamma')).toBeInTheDocument();
  expect(screen.getByText('@delta')).toBeInTheDocument();
  expect(screen.queryByText('t.me/Alpha')).toBeNull();
  expect(screen.getAllByText('@alpha')).toHaveLength(1);
  expect(screen.getByText('Не сохранено')).toBeInTheDocument();
  // Everything is in now: the second press has nothing to add.
  expect(add).toBeDisabled();
});

test('saving the list as a category posts its name and chats', async () => {
  routeApi();
  open();
  const user = userEvent.setup();

  expect(await screen.findByText(/Категорий пока нет/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Управлять' })).toBeNull();
  await user.click(screen.getByRole('button', { name: 'Сохранить как категорию' }));
  await user.type(screen.getByLabelText('Название категории'), 'Мои чаты');
  await user.click(screen.getByRole('button', { name: 'Сохранить категорию' }));

  await waitFor(() => {
    expect(callsTo(COLLECTIONS, 'POST')).toHaveLength(1);
  });
  const body = (await callsTo(COLLECTIONS, 'POST')[0]!.clone().json()) as {
    name: string;
    targets: string[];
  };
  expect(body).toEqual({ name: 'Мои чаты', targets: ['@alpha', 't.me/+AbCdEfGh123'] });
  expect(
    await screen.findByRole('button', { name: 'Добавить в рассылку: Мои чаты' }),
  ).toBeInTheDocument();
  expect(screen.queryByLabelText('Название категории')).toBeNull();
});

test('a campaign with no chats cannot be saved as a category', async () => {
  routeApi();
  renderPage(
    <SettingsDialog
      read={{ ...SETTINGS_READ, settings: { targets: [], messages: [] } }}
      fleet={FLEET as AccountRead[]}
      onClose={vi.fn()}
      onSaved={vi.fn()}
    />,
  );

  expect(await screen.findByRole('button', { name: 'Сохранить как категорию' })).toBeDisabled();
});

test('the editor renames a category, drops and adds chats, and saves the whole list', async () => {
  routeApi({ collections: [CRYPTO] });
  open();
  const user = userEvent.setup();

  await user.click(await screen.findByRole('button', { name: 'Управлять' }));
  const dialog = screen.getByRole('dialog', { name: 'Управлять категориями' });
  const name = within(dialog).getByLabelText('Название категории');
  await user.clear(name);
  await user.type(name, 'Крипта 2');
  await user.click(within(dialog).getByLabelText('Убрать t.me/Alpha'));
  await user.click(within(dialog).getByRole('button', { name: '+ Добавить чаты' }));
  await user.type(within(dialog).getByLabelText('Добавить чаты'), '@beta !!bad');
  await user.click(within(dialog).getByRole('button', { name: 'Добавить' }));
  expect(await within(dialog).findByText('@beta')).toBeInTheDocument();
  expect(within(dialog).getByText('Не распознаны: !!bad')).toBeInTheDocument();

  await user.click(within(dialog).getByRole('button', { name: 'Сохранить категорию Крипта' }));

  await waitFor(() => {
    expect(callsTo(`${COLLECTIONS}/k1`, 'PUT')).toHaveLength(1);
  });
  const body = (await callsTo(`${COLLECTIONS}/k1`, 'PUT')[0]!.clone().json()) as {
    name: string;
    targets: string[];
  };
  expect(body).toEqual({ name: 'Крипта 2', targets: ['t.me/gamma', '@delta', '@beta'] });
  expect(await within(dialog).findByLabelText('Удалить категорию Крипта 2')).toBeInTheDocument();
});

test('deleting a category asks first', async () => {
  routeApi({ collections: [CRYPTO] });
  open();
  const user = userEvent.setup();

  await user.click(await screen.findByRole('button', { name: 'Управлять' }));
  await user.click(screen.getByLabelText('Удалить категорию Крипта'));
  const question = screen.getByRole('dialog', { name: 'Удалить категорию «Крипта»?' });
  expect(callsTo(`${COLLECTIONS}/k1`, 'DELETE')).toHaveLength(0);
  await user.click(within(question).getByRole('button', { name: 'Удалить' }));

  await waitFor(() => {
    expect(callsTo(`${COLLECTIONS}/k1`, 'DELETE')).toHaveLength(1);
  });
  await waitFor(() => {
    expect(screen.queryByRole('button', { name: 'Добавить в рассылку: Крипта' })).toBeNull();
  });
});
