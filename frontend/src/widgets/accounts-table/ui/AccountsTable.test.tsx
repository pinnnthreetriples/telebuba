import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import type { RowSelectionState, SortingState } from '@tanstack/react-table';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { AccountsTable } from './AccountsTable';

const ACCOUNTS: AccountRead[] = [
  {
    account_id: 'acc-1',
    label: 'Main',
    status: 'alive',
    username: 'mainuser',
    proxy_country_code: 'RU',
    last_checked_at: '2026-06-28',
    created_at: 'now',
    updated_at: 'now',
  },
  { account_id: 'acc-2', status: 'new', created_at: 'now', updated_at: 'now' },
];

function Harness({
  onCheck = vi.fn(),
  onDelete = vi.fn(),
}: {
  onCheck?: (id: string) => void;
  onDelete?: (id: string) => void;
}) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  return (
    <AccountsTable
      data={ACCOUNTS}
      sorting={sorting}
      onSortingChange={setSorting}
      rowSelection={rowSelection}
      onRowSelectionChange={setRowSelection}
      onCheck={onCheck}
      onDelete={onDelete}
      busyId={null}
    />
  );
}

test('renders a row per account with label and country flag', () => {
  const { container } = render(<Harness />);
  expect(screen.getByText('acc-1')).toBeInTheDocument();
  expect(screen.getByText('Main')).toBeInTheDocument();
  expect(screen.getByText('acc-2')).toBeInTheDocument();
  expect(container.querySelector('.fi-ru')).not.toBeNull();
});

test('fires the row actions', async () => {
  const onCheck = vi.fn();
  const onDelete = vi.fn();
  render(<Harness onCheck={onCheck} onDelete={onDelete} />);
  await userEvent.click(screen.getAllByText('Проверить')[0]!);
  await userEvent.click(screen.getAllByText('Удалить')[0]!);
  expect(onCheck).toHaveBeenCalledWith('acc-1');
  expect(onDelete).toHaveBeenCalledWith('acc-1');
});

test('sorts when a column header is clicked and selects rows', async () => {
  const { container } = render(<Harness />);
  await userEvent.click(screen.getByText('Аккаунт'));
  expect(screen.getByText(/Аккаунт/).textContent).toMatch(/[↑↓]/);
  const checkboxes = container.querySelectorAll('input[type="checkbox"]');
  await userEvent.click(checkboxes[1]!); // first data row
  expect((checkboxes[1] as HTMLInputElement).checked).toBe(true);
});
