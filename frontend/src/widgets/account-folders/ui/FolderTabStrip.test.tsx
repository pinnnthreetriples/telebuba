import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountFolders } from '@/shared/api';

import { FolderTabStrip, type SelectAllState } from './FolderTabStrip';

const FOLDERS: AccountFolders = {
  items: [
    { folder_id: 'f-main', name: 'Основные', account_count: 5, created_at: 'now' },
    { folder_id: 'f-res', name: 'Резерв', account_count: 2, created_at: 'now' },
  ],
  total_count: 12,
  unfiled_count: 3,
};

const NONE_SELECTED: SelectAllState = {
  checked: false,
  indeterminate: false,
  disabled: false,
  onToggle: () => undefined,
};

function renderStrip(overrides: Partial<Parameters<typeof FolderTabStrip>[0]> = {}) {
  const props = {
    view: 'all',
    onView: vi.fn(),
    folders: FOLDERS,
    selectAll: NONE_SELECTED,
    dropFolderId: null,
    onCreate: vi.fn(),
    onSettings: vi.fn(),
    actions: <button type="button">toolbar</button>,
    ...overrides,
  };
  render(<FolderTabStrip {...props} />);
  return props;
}

test('tabs run all → folders → unfiled, and only the active tab shows its count', () => {
  renderStrip({ view: 'f-main' });
  const tabs = screen.getAllByRole('tab');
  expect(tabs.map((tab) => tab.textContent)).toEqual([
    'Все аккаунты',
    'Основные5',
    'Резерв',
    'Без папки',
  ]);
  expect(screen.getByRole('tab', { name: /^Основные/ })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('tab', { name: /^Основные/ })).toHaveClass('bg-surface');
  expect(screen.getByText('toolbar')).toBeInTheDocument();
});

test('clicking or arrowing to a tab opens that view', async () => {
  const props = renderStrip();
  await userEvent.click(screen.getByRole('tab', { name: 'Без папки' }));
  expect(props.onView).toHaveBeenLastCalledWith('unfiled');

  fireEvent.keyDown(screen.getByRole('tab', { name: /Все аккаунты/ }), { key: 'ArrowRight' });
  expect(props.onView).toHaveBeenLastCalledWith('f-main');
  fireEvent.keyDown(screen.getByRole('tab', { name: /Все аккаунты/ }), { key: 'ArrowLeft' });
  expect(props.onView).toHaveBeenLastCalledWith('unfiled');
});

test('folder settings open from the gear and from a right-click, only for a real folder', () => {
  const props = renderStrip({ view: 'f-res' });
  fireEvent.click(screen.getByRole('button', { name: 'Настройки папки «Резерв»' }));
  expect(props.onSettings).toHaveBeenLastCalledWith('f-res');

  fireEvent.contextMenu(screen.getByRole('tab', { name: 'Основные' }));
  expect(props.onSettings).toHaveBeenLastCalledWith('f-main');

  vi.mocked(props.onSettings).mockClear();
  fireEvent.contextMenu(screen.getByRole('tab', { name: 'Без папки' }));
  expect(props.onSettings).not.toHaveBeenCalled();
});

test('the virtual views have no gear and take no drops', () => {
  renderStrip({ view: 'unfiled' });
  // The gear stays mounted so its slot can animate closed, but it is inert there:
  // out of the tab order and the accessibility tree.
  const gear = screen.getByTitle('Переименовать или удалить папку');
  expect(gear.closest('[inert]')).not.toBeNull();
  expect(screen.getByRole('tab', { name: /Без папки/ })).not.toHaveAttribute('data-folder-drop');
  expect(screen.getByRole('tab', { name: 'Основные' })).toHaveAttribute(
    'data-folder-drop',
    'f-main',
  );
});

test('the create button asks for a new folder', async () => {
  const props = renderStrip();
  await userEvent.click(screen.getByRole('button', { name: 'Создать папку' }));
  expect(props.onCreate).toHaveBeenCalledTimes(1);
});

test('a folder under a drag is tinted', () => {
  renderStrip({ dropFolderId: 'f-res' });
  expect(screen.getByRole('tab', { name: 'Резерв' })).toHaveClass('bg-info-tint');
  expect(screen.getByRole('tab', { name: 'Основные' })).not.toHaveClass('bg-info-tint');
});

test('select-all reflects a partial selection as indeterminate and toggles', async () => {
  const onToggle = vi.fn();
  renderStrip({ selectAll: { checked: false, indeterminate: true, disabled: false, onToggle } });
  const box = screen.getByRole<HTMLInputElement>('checkbox', { name: 'Выбрать все аккаунты' });
  expect(box.indeterminate).toBe(true);
  expect(box.checked).toBe(false);
  await userEvent.click(box);
  expect(onToggle).toHaveBeenCalledTimes(1);
});
