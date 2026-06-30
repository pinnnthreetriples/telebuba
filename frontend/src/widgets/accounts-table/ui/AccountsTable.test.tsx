import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
    proxy_id: 'p1',
    proxy_type: 'socks5',
    proxy_status: 'tcp_working',
    proxy_country_code: 'RU',
    last_checked_at: '2026-06-28',
    created_at: 'now',
    updated_at: 'now',
  },
  { account_id: 'acc-2', status: 'new', created_at: 'now', updated_at: 'now' },
];

test('renders a row per account with handle and country flag', () => {
  const { container } = render(
    <AccountsTable data={ACCOUNTS} onCheck={vi.fn()} onDelete={vi.fn()} busyId={null} />,
  );
  expect(screen.getByText('acc-1')).toBeInTheDocument();
  expect(screen.getByText('@mainuser')).toBeInTheDocument();
  expect(screen.getByText('acc-2')).toBeInTheDocument();
  expect(container.querySelector('.fi-ru')).not.toBeNull();
});

test('fires the row actions for the clicked account', async () => {
  const onCheck = vi.fn();
  const onDelete = vi.fn();
  render(<AccountsTable data={ACCOUNTS} onCheck={onCheck} onDelete={onDelete} busyId={null} />);
  await userEvent.click(screen.getAllByTitle('Проверить')[0]!);
  await userEvent.click(screen.getAllByTitle('Удалить')[0]!);
  expect(onCheck).toHaveBeenCalledWith('acc-1');
  expect(onDelete).toHaveBeenCalledWith('acc-1');
});
