import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountFolder } from '@/shared/api';
import { Toaster } from '@/shared/ui';

import { useFolderActions } from '../model/useFolderActions';
import { FolderDialog, type FolderDialogState } from './FolderDialog';

const FOLDERS: AccountFolder[] = [
  { folder_id: 'f-main', name: 'Основные', account_count: 2, created_at: 'now' },
  { folder_id: 'f-res', name: 'Резерв', account_count: 0, created_at: 'now' },
];

interface Call {
  method: string;
  path: string;
  body: unknown;
}

function json(body: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

// The folder endpoints, recording every write.
function routeFolderApi(): Call[] {
  const calls: Call[] = [];
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const url = new URL(request.url);
    const text = await request.clone().text();
    const body: unknown = text ? JSON.parse(text) : undefined;
    if (request.method !== 'GET') calls.push({ method: request.method, path: url.pathname, body });
    const named = body as { name?: string; account_ids?: string[] } | undefined;
    if (url.pathname === '/api/v1/accounts' && request.method === 'GET') {
      return url.searchParams.get('cursor')
        ? json({ items: [{ account_id: 'acc-3' }], next_cursor: null, total: 3 })
        : json({
            items: [{ account_id: 'acc-1' }, { account_id: 'acc-2' }],
            next_cursor: 'c2',
            total: 3,
          });
    }
    if (url.pathname === '/api/v1/account-folders' && request.method === 'POST') {
      if (named?.name === 'Занято') {
        return json({ error: { code: 'conflict', message: 'folder_name_taken' } }, 409);
      }
      return json({ folder_id: 'f-new', name: named?.name, account_count: 0, created_at: 'now' });
    }
    if (url.pathname.endsWith('/accounts') || url.pathname.endsWith('/accounts/remove')) {
      return json({ folder_id: url.pathname.split('/')[4], account_ids: named?.account_ids });
    }
    if (request.method === 'DELETE') return json(null, 204);
    if (request.method === 'PATCH') {
      return json({ folder_id: 'f-main', name: named?.name, account_count: 2, created_at: 'now' });
    }
    return json({ items: FOLDERS, total_count: 2, unfiled_count: 0 });
  });
  return calls;
}

function Harness({ initial }: { initial: FolderDialogState }) {
  const [state, setState] = useState<FolderDialogState>(initial);
  const actions = useFolderActions(FOLDERS);
  return (
    <>
      <FolderDialog state={state} onChange={setState} folders={FOLDERS} actions={actions} />
      <Toaster />
    </>
  );
}

function renderDialog(initial: FolderDialogState) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Harness initial={initial} />
    </QueryClientProvider>,
  );
}

async function undoIn(text: string) {
  const toast = (await screen.findByText(text)).closest('[role="status"]') as HTMLElement;
  await userEvent.click(within(toast).getByRole('button', { name: 'Отменить' }));
}

// The toast queue is module state: close whatever a test left on screen.
afterEach(() => {
  for (const close of screen.queryAllByRole('button', { name: 'Скрыть' })) fireEvent.click(close);
});

test('create checks the name before sending it: empty, then taken ignoring case', async () => {
  const calls = routeFolderApi();
  renderDialog({ kind: 'create' });
  const field = screen.getByPlaceholderText('Например, Проект Б');
  expect(field).toHaveFocus();

  await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  expect(screen.getByRole('alert')).toHaveTextContent('Введите название папки.');

  await userEvent.type(field, '  основные ');
  await userEvent.keyboard('{Enter}');
  expect(screen.getByRole('alert')).toHaveTextContent('Такая папка уже существует.');
  expect(calls).toEqual([]);
});

test('a name the server refuses as taken stays in the dialog with the inline message', async () => {
  routeFolderApi();
  renderDialog({ kind: 'create' });
  await userEvent.type(screen.getByPlaceholderText('Например, Проект Б'), 'Занято');
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() => {
    expect(screen.getByRole('alert')).toHaveTextContent('Такая папка уже существует.');
  });
  expect(screen.getByRole('dialog')).toBeInTheDocument();
});

test('create sends the trimmed name, closes, and its undo deletes the new folder', async () => {
  const calls = routeFolderApi();
  renderDialog({ kind: 'create' });
  await userEvent.type(screen.getByPlaceholderText('Например, Проект Б'), ' Проект Б ');
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

  await undoIn('Создана папка «Проект Б»');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await waitFor(() => {
    expect(calls).toEqual([
      { method: 'POST', path: '/api/v1/account-folders', body: { name: 'Проект Б' } },
      { method: 'DELETE', path: '/api/v1/account-folders/f-new', body: undefined },
    ]);
  });
  expect(await screen.findByText('Отменено')).toBeInTheDocument();
});

test('settings → rename prefills the name, and the undo renames it back', async () => {
  const calls = routeFolderApi();
  renderDialog({ kind: 'settings', folderId: 'f-main' });
  await userEvent.click(screen.getByRole('button', { name: 'Переименовать' }));
  const field = screen.getByPlaceholderText('Например, Проект Б');
  expect(field).toHaveValue('Основные');

  await userEvent.clear(field);
  await userEvent.type(field, 'Главные');
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await undoIn('Название папки изменено');
  await waitFor(() => {
    expect(calls.map((call) => call.body)).toEqual([{ name: 'Главные' }, { name: 'Основные' }]);
  });
  expect(calls.every((call) => call.path === '/api/v1/account-folders/f-main')).toBe(true);
});

test('delete asks first, and its undo recreates the folder with every member', async () => {
  const calls = routeFolderApi();
  renderDialog({ kind: 'settings', folderId: 'f-main' });
  await userEvent.click(screen.getByRole('button', { name: 'Удалить папку' }));
  expect(
    screen.getByText(
      'Удалить папку «Основные»? Аккаунты останутся в общем списке и в других папках.',
    ),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Удалить папку' }));

  await undoIn('Папка удалена, аккаунты на месте');
  await waitFor(() => {
    expect(calls).toEqual([
      { method: 'DELETE', path: '/api/v1/account-folders/f-main', body: undefined },
      { method: 'POST', path: '/api/v1/account-folders', body: { name: 'Основные' } },
      {
        method: 'POST',
        path: '/api/v1/account-folders/f-new/accounts',
        body: { account_ids: ['acc-1', 'acc-2', 'acc-3'] },
      },
    ]);
  });
});

test('the settings dialog closes from its × without writing anything', async () => {
  const calls = routeFolderApi();
  renderDialog({ kind: 'settings', folderId: 'f-res' });
  expect(screen.getByRole('heading', { name: 'Резерв' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Закрыть окно' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(calls).toEqual([]);
});
