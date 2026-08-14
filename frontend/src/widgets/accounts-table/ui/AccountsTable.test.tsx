import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';
import type { FeedbackResult } from '@/shared/lib';

import { AccountsTable } from './AccountsTable';

const NONE_BUSY = new Set<string>();
const NO_RESULTS: Record<string, FeedbackResult> = {};

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
    <AccountsTable
      data={ACCOUNTS}
      onCheck={vi.fn()}
      onDelete={vi.fn()}
      busyIds={NONE_BUSY}
      checkResults={NO_RESULTS}
    />,
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
    <AccountsTable
      data={named}
      onCheck={vi.fn()}
      onDelete={vi.fn()}
      busyIds={NONE_BUSY}
      checkResults={NO_RESULTS}
    />,
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
    <AccountsTable
      data={named}
      onCheck={vi.fn()}
      onDelete={vi.fn()}
      busyIds={NONE_BUSY}
      checkResults={NO_RESULTS}
    />,
  );
  // A failed image load swaps the <img> for the mono initials avatar.
  fireEvent.error(container.querySelector('img')!);
  expect(container.querySelector('img')).toBeNull();
  expect(screen.getByText('AL')).toBeInTheDocument();
});

test('renders the real trust score and device, dashes when absent', () => {
  render(
    <AccountsTable
      data={ACCOUNTS}
      onCheck={vi.fn()}
      onDelete={vi.fn()}
      busyIds={NONE_BUSY}
      checkResults={NO_RESULTS}
    />,
  );
  // acc-1 carries a backend trust score + device fingerprint
  expect(screen.getByText('82')).toBeInTheDocument();
  expect(screen.getByText('iPhone 13 · iOS 17.2')).toBeInTheDocument();
  // acc-2 has neither → both columns fall back to an em dash
  expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
});

test('fires the row actions for the clicked account', async () => {
  const onCheck = vi.fn();
  const onDelete = vi.fn();
  render(
    <AccountsTable
      data={ACCOUNTS}
      onCheck={onCheck}
      onDelete={onDelete}
      busyIds={NONE_BUSY}
      checkResults={NO_RESULTS}
    />,
  );
  await userEvent.click(screen.getAllByTitle('Проверить')[0]!);
  await userEvent.click(screen.getAllByTitle('Удалить')[0]!);
  expect(onCheck).toHaveBeenCalledWith('acc-1');
  expect(onDelete).toHaveBeenCalledWith('acc-1');
});

test('each row wears its own check verdict', () => {
  render(
    <AccountsTable
      data={ACCOUNTS}
      onCheck={vi.fn()}
      onDelete={vi.fn()}
      busyIds={NONE_BUSY}
      checkResults={{ 'acc-1': 'ok', 'acc-2': 'err' }}
    />,
  );
  // Keyed per row like busyIds: two checks can settle at different times, so the
  // verdicts must not share one slot. Asserted by accessible name, which is also
  // the thing a screen reader gets — the fill and the glyph alone say nothing.
  expect(screen.getByLabelText('Аккаунт живой')).toBeInTheDocument();
  expect(screen.getByLabelText('Аккаунт недоступен')).toBeInTheDocument();
});

test('a re-checked row drops its old verdict instead of spinning on top of it', () => {
  const { container } = render(
    <AccountsTable
      data={ACCOUNTS}
      onCheck={vi.fn()}
      onDelete={vi.fn()}
      busyIds={new Set(['acc-1'])}
      checkResults={{ 'acc-1': 'ok' }}
    />,
  );
  // The button re-enables as soon as its spinner clears, so a second click inside
  // the flash window would render the spinner over the previous answer's fill.
  expect(container.querySelector('button.bg-success')).toBeNull();
  expect(screen.queryByLabelText('Аккаунт живой')).not.toBeInTheDocument();
});

test('busy state is per row, and more than one row can be busy', () => {
  render(
    <AccountsTable
      data={ACCOUNTS}
      onCheck={vi.fn()}
      onDelete={vi.fn()}
      busyIds={new Set(['acc-2'])}
      checkResults={NO_RESULTS}
    />,
  );
  // Only the row in the set is disabled — a single busy id could not say "row 2
  // is busy, row 1 is not" once a second row had been acted on.
  expect(screen.getAllByTitle('Проверить')[0]).toBeEnabled();
  expect(screen.getAllByTitle('Проверить')[1]).toBeDisabled();

  render(
    <AccountsTable
      data={ACCOUNTS}
      onCheck={vi.fn()}
      onDelete={vi.fn()}
      busyIds={new Set(['acc-1', 'acc-2'])}
      checkResults={NO_RESULTS}
    />,
  );
  for (const button of screen.getAllByTitle('Проверить').slice(2)) {
    expect(button).toBeDisabled();
  }
});

test('a row opens from the keyboard', async () => {
  const onOpen = vi.fn();
  render(
    <AccountsTable
      data={ACCOUNTS}
      onCheck={vi.fn()}
      onDelete={vi.fn()}
      onOpen={onOpen}
      onProfile={vi.fn()}
      busyIds={NONE_BUSY}
      checkResults={NO_RESULTS}
    />,
  );
  // The row is the ONLY entry point to the account-edit view (the pencil opens
  // the profile modal), and it had no tabIndex and no key handler at all: with a
  // keyboard, session/proxy/device/signals/actions were unreachable.
  const row = screen.getByText('@mainuser').closest('tr')!;
  expect(row).toHaveAttribute('tabindex', '0');
  row.focus();
  await userEvent.keyboard('{Enter}');
  expect(onOpen).toHaveBeenCalledWith(ACCOUNTS[0]);

  onOpen.mockClear();
  await userEvent.keyboard(' ');
  expect(onOpen).toHaveBeenCalledWith(ACCOUNTS[0]);

  // A key press on an action button inside the row must not also open the row.
  onOpen.mockClear();
  (screen.getAllByTitle('Проверить')[0] as HTMLElement).focus();
  await userEvent.keyboard('{Enter}');
  expect(onOpen).not.toHaveBeenCalled();
});

test('opens the clicked row and does not bubble action clicks to the row', async () => {
  const onOpen = vi.fn();
  render(
    <AccountsTable
      data={ACCOUNTS}
      onCheck={vi.fn()}
      onDelete={vi.fn()}
      onOpen={onOpen}
      busyIds={NONE_BUSY}
      checkResults={NO_RESULTS}
    />,
  );
  await userEvent.click(screen.getByText('@mainuser'));
  expect(onOpen).toHaveBeenCalledWith(ACCOUNTS[0]);
  // an action button stops propagation → the row's onOpen must not double-fire
  onOpen.mockClear();
  await userEvent.click(screen.getAllByTitle('Проверить')[0]!);
  expect(onOpen).not.toHaveBeenCalled();
});
