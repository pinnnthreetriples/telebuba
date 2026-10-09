import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';
import { Toaster } from '@/shared/ui';

import { AccountsPage } from './AccountsPage';
import { jsonResponse, renderWithClient } from './AccountsPage.test-helpers';

const FOLDERS = {
  items: [
    { folder_id: 'f-main', name: 'Основные', account_count: 1, created_at: 'now' },
    { folder_id: 'f-empty', name: 'Пустая', account_count: 0, created_at: 'now' },
  ],
  total_count: 2,
  unfiled_count: 1,
};

function row(id: string, folderIds: string[]): AccountRead {
  return {
    account_id: id,
    status: 'alive',
    folder_ids: folderIds,
    created_at: 'now',
    updated_at: 'now',
  };
}

const ROWS = [row('acc-1', ['f-main']), row('acc-2', [])];

// Every request the page made, by path, with its query and body.
const requests: { method: string; path: string; params: URLSearchParams; body: unknown }[] = [];

function routeFolderPage() {
  requests.length = 0;
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const url = new URL(request.url);
    const text = await request.clone().text();
    const body: unknown = text ? JSON.parse(text) : undefined;
    requests.push({ method: request.method, path: url.pathname, params: url.searchParams, body });
    if (url.pathname === '/api/v1/account-folders') return jsonResponse(FOLDERS);
    if (url.pathname === '/api/v1/accounts/filter-options') {
      return jsonResponse({
        phone_codes: [{ calling_code: 49, country_code: 'DE', count: 1 }],
        proxy_countries: [],
        no_proxy_count: 2,
      });
    }
    if (url.pathname === '/api/v1/accounts') {
      const folder = url.searchParams.get('folder');
      const items =
        folder === 'f-empty' || url.searchParams.has('min_trust')
          ? []
          : folder === 'f-main'
            ? ROWS.slice(0, 1)
            : ROWS;
      return jsonResponse({ items, next_cursor: null, total: items.length });
    }
    if (url.pathname.startsWith('/api/v1/account-folders/')) {
      const ids = (body as { account_ids: string[] }).account_ids;
      return jsonResponse({ folder_id: 'f-main', account_ids: ids });
    }
    if (url.pathname === '/api/v1/accounts/stats') {
      return jsonResponse({ total: 2, active: 2, idle: 0, needs_code: 0, problem: 0 });
    }
    if (url.pathname === '/api/v1/auth/me') return jsonResponse({ id: 'u-1' });
    return jsonResponse(null);
  });
}

function lastList(): URLSearchParams {
  const lists = requests.filter((request) => request.path === '/api/v1/accounts');
  return lists[lists.length - 1]!.params;
}

async function renderPage() {
  routeFolderPage();
  renderWithClient(<AccountsPage />);
  render(<Toaster />);
  await screen.findByText('acc-2');
}

afterEach(() => {
  for (const close of screen.queryAllByRole('button', { name: 'Скрыть' })) fireEvent.click(close);
  vi.restoreAllMocks();
});

test('a folder tab narrows the list to that folder on the server', async () => {
  await renderPage();
  expect(lastList().has('folder')).toBe(false);

  await userEvent.click(screen.getByRole('tab', { name: 'Основные' }));
  await waitFor(() => {
    expect(lastList().get('folder')).toBe('f-main');
  });
  await waitFor(() => {
    expect(screen.queryByText('acc-2')).not.toBeInTheDocument();
  });
  expect(screen.getByRole('tab', { name: /Основные/ })).toHaveTextContent('Основные1');

  await userEvent.click(screen.getByRole('tab', { name: 'Без папки' }));
  await waitFor(() => {
    expect(lastList().get('folder')).toBe('unfiled');
  });
});

test('an empty folder says so inside the panel', async () => {
  await renderPage();
  await userEvent.click(screen.getByRole('tab', { name: 'Пустая' }));
  expect(await screen.findByText('В этой папке пока нет аккаунтов')).toBeInTheDocument();
});

test('filters go to the server, show as chips, and «Сбросить фильтры» clears them', async () => {
  await renderPage();
  await userEvent.click(screen.getByRole('button', { name: 'Расширенные фильтры' }));
  await userEvent.click(screen.getByRole('radio', { name: '+49 · Германия' }));
  await waitFor(() => {
    expect(lastList().get('phone_code')).toBe('49');
  });
  await userEvent.click(screen.getByRole('radio', { name: 'Простаивают' }));
  await waitFor(() => {
    expect(lastList().get('status')).toBe('idle');
  });
  await userEvent.click(screen.getByRole('radio', { name: '90+' }));
  await waitFor(() => {
    expect(lastList().get('min_trust')).toBe('90');
  });
  await userEvent.click(screen.getByRole('button', { name: 'Показать' }));

  expect(screen.getByText('Trust ≥ 90')).toBeInTheDocument();
  expect(await screen.findByText('Ничего не найдено')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Сбросить фильтры' }));
  await waitFor(() => {
    expect(lastList().get('status')).toBe('all');
  });
  expect(lastList().has('phone_code')).toBe(false);
  expect(screen.queryByText('Trust ≥ 90')).not.toBeInTheDocument();
});

test('select-all goes indeterminate on a partial selection and selects the page', async () => {
  await renderPage();
  const all = screen.getByRole<HTMLInputElement>('checkbox', { name: 'Выбрать все аккаунты' });
  const first = screen.getByRole<HTMLInputElement>('checkbox', { name: 'Выбрать acc-1' });

  await userEvent.click(first);
  expect(all.indeterminate).toBe(true);
  // Selecting is the checkbox's job: the row did not open the account editor.
  expect(screen.getByRole('table')).toBeInTheDocument();

  await userEvent.click(all);
  expect(all.checked).toBe(true);
  expect(screen.getByRole<HTMLInputElement>('checkbox', { name: 'Выбрать acc-2' }).checked).toBe(
    true,
  );
  await userEvent.click(all);
  expect(first.checked).toBe(false);
});

test('dropping the selection on a folder adds it, and the toast undoes exactly that', async () => {
  await renderPage();
  await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать все аккаунты' }));
  const folderTab = screen.getByRole('tab', { name: 'Основные' });

  fireEvent.pointerDown(screen.getByRole('button', { name: 'Перетащить acc-2 в папку' }), {
    button: 0,
    pointerId: 1,
    clientX: 0,
    clientY: 0,
  });
  vi.spyOn(document, 'elementFromPoint').mockReturnValue(folderTab);
  fireEvent.pointerMove(document, { pointerId: 1, clientX: 40, clientY: 0 });
  expect(folderTab).toHaveClass('bg-info-tint');
  fireEvent.pointerUp(document, { pointerId: 1 });

  const toast = (await screen.findByText('2 аккаунта → «Основные»')).closest(
    '[role="status"]',
  ) as HTMLElement;
  expect(
    requests.find((request) => request.path === '/api/v1/account-folders/f-main/accounts')?.body,
  ).toEqual({ account_ids: ['acc-1', 'acc-2'] });

  await userEvent.click(within(toast).getByRole('button', { name: 'Отменить' }));
  await waitFor(() => {
    expect(
      requests.find((request) => request.path === '/api/v1/account-folders/f-main/accounts/remove')
        ?.body,
    ).toEqual({ account_ids: ['acc-1', 'acc-2'] });
  });
});

test('a row names its folders under the handle, and a name opens that folder', async () => {
  await renderPage();
  const tag = screen.getByRole('button', { name: 'Папки: Основные' });
  expect(tag).toHaveTextContent('1');
  expect(tag).toHaveAttribute('title', 'Основные');

  await userEvent.click(tag);
  await userEvent.click(screen.getByRole('button', { name: 'Основные' }));
  await waitFor(() => {
    expect(lastList().get('folder')).toBe('f-main');
  });
  expect(screen.getByRole('tab', { name: /Основные/ })).toHaveAttribute('aria-selected', 'true');
});

test('the create button opens the new-folder dialog', async () => {
  await renderPage();
  await userEvent.click(screen.getByRole('button', { name: 'Создать папку' }));
  expect(screen.getByRole('dialog', { name: 'Создать папку' })).toBeInTheDocument();
});
