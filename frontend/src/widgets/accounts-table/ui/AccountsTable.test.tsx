import { fireEvent, render, screen } from '@testing-library/react';
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
    trust_score: 82,
    device_model: 'iPhone 13',
    device_system_version: 'iOS 17.2',
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

test('shows the telegram name on top, username below, and the captured photo', () => {
  const named: AccountRead[] = [
    {
      account_id: 'acc-3',
      status: 'alive',
      first_name: 'Vika',
      last_name: 'M',
      username: 'vikamn',
      avatar_etag: 'abc123',
      created_at: 'now',
      updated_at: 'now',
    },
  ];
  const { container } = render(
    <AccountsTable data={named} onCheck={vi.fn()} onDelete={vi.fn()} busyId={null} />,
  );
  expect(screen.getByText('Vika M')).toBeInTheDocument();
  expect(screen.getByText('@vikamn')).toBeInTheDocument();
  const img = container.querySelector('img');
  expect(img?.getAttribute('src')).toBe('/api/v1/accounts/acc-3/avatar?v=abc123');
});

test('falls back to name initials when no photo is captured, and on a broken image', () => {
  const named: AccountRead[] = [
    {
      account_id: 'acc-4',
      status: 'alive',
      first_name: 'Ann',
      last_name: 'Lee',
      avatar_etag: 'zzz',
      created_at: 'now',
      updated_at: 'now',
    },
  ];
  const { container } = render(
    <AccountsTable data={named} onCheck={vi.fn()} onDelete={vi.fn()} busyId={null} />,
  );
  // A failed image load swaps the <img> for the mono initials avatar.
  fireEvent.error(container.querySelector('img')!);
  expect(container.querySelector('img')).toBeNull();
  expect(screen.getByText('AL')).toBeInTheDocument();
});

test('renders the real trust score and device, dashes when absent', () => {
  render(<AccountsTable data={ACCOUNTS} onCheck={vi.fn()} onDelete={vi.fn()} busyId={null} />);
  // acc-1 carries a backend trust score + device fingerprint
  expect(screen.getByText('82')).toBeInTheDocument();
  expect(screen.getByText('iPhone 13 · iOS 17.2')).toBeInTheDocument();
  // acc-2 has neither → both columns fall back to an em dash
  expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
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

test('opens the clicked row and does not bubble action clicks to the row', async () => {
  const onOpen = vi.fn();
  render(
    <AccountsTable
      data={ACCOUNTS}
      onCheck={vi.fn()}
      onDelete={vi.fn()}
      onOpen={onOpen}
      busyId={null}
    />,
  );
  await userEvent.click(screen.getByText('@mainuser'));
  expect(onOpen).toHaveBeenCalledWith(ACCOUNTS[0]);
  // an action button stops propagation → the row's onOpen must not double-fire
  onOpen.mockClear();
  await userEvent.click(screen.getAllByTitle('Проверить')[0]!);
  expect(onOpen).not.toHaveBeenCalled();
});
