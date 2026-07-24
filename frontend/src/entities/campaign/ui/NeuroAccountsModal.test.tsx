import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { NeuroAccountsModal, type NeuroAccountRow } from './NeuroAccountsModal';

const ACCOUNTS: NeuroAccountRow[] = [
  { account_id: 'a1', name: 'Vika Ix', linked: true, pinned_channels: ['@crypto'] },
  { account_id: 'a2', name: '+79990000002', linked: false, pinned_channels: [] },
];
const CHANNELS = ['@crypto', '@news'];

test('assigns an idle account, confirms removal, and closes', async () => {
  const onClose = vi.fn();
  const onPick = vi.fn();
  const onRemove = vi.fn();
  render(
    <NeuroAccountsModal
      accounts={ACCOUNTS}
      channels={CHANNELS}
      onClose={onClose}
      onPick={onPick}
      onRemove={onRemove}
      onChannelChange={vi.fn()}
    />,
  );
  expect(screen.getByText('Аккаунты в нейрокомментинге')).toBeInTheDocument();
  // the row shows the account's Telegram display name
  expect(screen.getByText('Vika Ix')).toBeInTheDocument();
  // an already-assigned account shows its single channel in the dropdown trigger
  expect(screen.getByLabelText('Каналы аккаунта')).toHaveTextContent('@crypto');

  // assign the idle account to the campaign
  await userEvent.click(screen.getByText('Добавить в кампанию'));
  expect(onPick).toHaveBeenCalledWith('a2');

  // removing asks for confirmation before calling onRemove
  await userEvent.click(screen.getAllByLabelText('Убрать из нейрокомментинга')[0]!);
  expect(onRemove).not.toHaveBeenCalled();
  await userEvent.click(screen.getByText('Убрать', { selector: 'button' }));
  expect(onRemove).toHaveBeenCalledWith('a1');

  await userEvent.click(screen.getByText('Готово'));
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('the dropdown reflects the account subset and offers all channels', async () => {
  render(
    <NeuroAccountsModal
      accounts={ACCOUNTS}
      channels={CHANNELS}
      onClose={vi.fn()}
      onPick={vi.fn()}
      onRemove={vi.fn()}
      onChannelChange={vi.fn()}
    />,
  );
  const trigger = screen.getByLabelText('Каналы аккаунта');
  // a one-channel subset shows the channel name
  expect(trigger).toHaveTextContent('@crypto');
  await userEvent.click(trigger);
  // the "all channels" row plus the campaign's channels
  const options = screen.getAllByRole('option').map((o) => o.textContent);
  expect(options).toEqual(['Все каналы', '@crypto', '@news']);
  // the account's channel is the selected option
  expect(screen.getByRole('option', { selected: true })).toHaveTextContent('@crypto');
});

test('an empty subset shows and selects "all channels"', async () => {
  render(
    <NeuroAccountsModal
      accounts={[{ account_id: 'a3', name: '+79990000003', linked: true, pinned_channels: [] }]}
      channels={CHANNELS}
      onClose={vi.fn()}
      onPick={vi.fn()}
      onRemove={vi.fn()}
      onChannelChange={vi.fn()}
    />,
  );
  const trigger = screen.getByLabelText('Каналы аккаунта');
  expect(trigger).toHaveTextContent('Все каналы');
  await userEvent.click(trigger);
  expect(screen.getByRole('option', { selected: true })).toHaveTextContent('Все каналы');
});

test('a multi-channel subset shows a count in the trigger', () => {
  render(
    <NeuroAccountsModal
      accounts={[
        {
          account_id: 'a3',
          name: '+79990000003',
          linked: true,
          pinned_channels: ['@crypto', '@news'],
        },
      ]}
      channels={CHANNELS}
      onClose={vi.fn()}
      onPick={vi.fn()}
      onRemove={vi.fn()}
      onChannelChange={vi.fn()}
    />,
  );
  expect(screen.getByLabelText('Каналы аккаунта')).toHaveTextContent('Каналов: 2');
});

test('toggling channels adds/removes; "all channels" clears the subset', async () => {
  const onChannelChange = vi.fn();
  render(
    <NeuroAccountsModal
      accounts={[
        { account_id: 'a3', name: '+79990000003', linked: true, pinned_channels: ['@crypto'] },
      ]}
      channels={CHANNELS}
      onClose={vi.fn()}
      onPick={vi.fn()}
      onRemove={vi.fn()}
      onChannelChange={onChannelChange}
    />,
  );
  const trigger = screen.getByLabelText('Каналы аккаунта');
  await userEvent.click(trigger);

  // an unselected channel is added to the subset (menu stays open — multi-select)
  await userEvent.click(screen.getByRole('option', { name: '@news' }));
  expect(onChannelChange).toHaveBeenLastCalledWith('a3', ['@crypto', '@news']);

  // toggling a selected channel removes it
  await userEvent.click(screen.getByRole('option', { name: '@crypto' }));
  expect(onChannelChange).toHaveBeenLastCalledWith('a3', []);

  // "Все каналы" clears the whole subset (= all channels)
  await userEvent.click(screen.getByRole('option', { name: 'Все каналы' }));
  expect(onChannelChange).toHaveBeenLastCalledWith('a3', []);
});

test('empty list shows the empty hint', () => {
  render(
    <NeuroAccountsModal
      accounts={[]}
      onClose={vi.fn()}
      onPick={vi.fn()}
      onRemove={vi.fn()}
      onChannelChange={vi.fn()}
    />,
  );
  expect(screen.getByText('Нет аккаунтов в нейрокомментинге')).toBeInTheDocument();
});

test('shows a success or error mark from the feedback map', () => {
  // Modal content is rendered via a portal onto document.body, not inside
  // the render() container — query the document instead.
  const { rerender } = render(
    <NeuroAccountsModal
      accounts={ACCOUNTS}
      onClose={vi.fn()}
      onPick={vi.fn()}
      onRemove={vi.fn()}
      onChannelChange={vi.fn()}
      feedback={{ a1: 'ok' }}
    />,
  );
  expect(document.querySelector('.text-success svg')).toBeInTheDocument();

  rerender(
    <NeuroAccountsModal
      accounts={ACCOUNTS}
      onClose={vi.fn()}
      onPick={vi.fn()}
      onRemove={vi.fn()}
      onChannelChange={vi.fn()}
      feedback={{ a1: 'err' }}
    />,
  );
  expect(document.querySelector('.text-danger svg')).toBeInTheDocument();
});
