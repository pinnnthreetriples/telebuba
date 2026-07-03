import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { NeuroAccountsModal, type NeuroAccountRow } from './NeuroAccountsModal';

const ACCOUNTS: NeuroAccountRow[] = [
  { account_id: 'a1', phone: '+79990000001', linked: true, pinned_channel: '@crypto' },
  { account_id: 'a2', phone: '+79990000002', linked: false, pinned_channel: null },
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
  // an already-assigned account shows its channel in a dropdown of the
  // campaign's channels
  expect(screen.getByText('@crypto')).toBeInTheDocument();

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

test('a linked account channel dropdown reflects the pin and offers all channels', () => {
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
  const select = screen.getByLabelText('Канал аккаунта') as HTMLSelectElement;
  expect(select).not.toBeDisabled();
  // the "all channels" sentinel plus the campaign's channels
  const options = Array.from(select.querySelectorAll('option')).map((o) => o.textContent);
  expect(options).toEqual(['Все каналы', '@crypto', '@news']);
  // the current value reflects the account's pin
  expect(select.value).toBe('@crypto');
});

test('an unpinned linked account selects "all channels"', () => {
  render(
    <NeuroAccountsModal
      accounts={[{ account_id: 'a3', phone: '+79990000003', linked: true, pinned_channel: null }]}
      channels={CHANNELS}
      onClose={vi.fn()}
      onPick={vi.fn()}
      onRemove={vi.fn()}
      onChannelChange={vi.fn()}
    />,
  );
  const select = screen.getByLabelText('Канал аккаунта') as HTMLSelectElement;
  expect(select.value).toBe('');
});

test('choosing a channel pins it; choosing "all channels" sends null', async () => {
  const onChannelChange = vi.fn();
  render(
    <NeuroAccountsModal
      accounts={[{ account_id: 'a3', phone: '+79990000003', linked: true, pinned_channel: null }]}
      channels={CHANNELS}
      onClose={vi.fn()}
      onPick={vi.fn()}
      onRemove={vi.fn()}
      onChannelChange={onChannelChange}
    />,
  );
  const select = screen.getByLabelText('Канал аккаунта');

  await userEvent.selectOptions(select, '@news');
  expect(onChannelChange).toHaveBeenLastCalledWith('a3', '@news');

  await userEvent.selectOptions(select, 'Все каналы');
  expect(onChannelChange).toHaveBeenLastCalledWith('a3', null);
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
