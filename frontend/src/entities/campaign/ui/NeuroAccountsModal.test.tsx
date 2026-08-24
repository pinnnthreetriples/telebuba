import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { beforeEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { NeuroAccountsModal, type NeuroAccountRow } from './NeuroAccountsModal';

// Every row now carries a limits chip, which reads GET /accounts/{id}/limits. One stub
// answers for all of them: the rows differ by account, the caps in these tests do not.
const LIMITS = {
  account_id: 'a1',
  joins: { limit: 20, used: 8, fleet_default: 20, overridden: false, resets_at: null },
  comments_per_hour: { limit: 10, used: 1, fleet_default: 10, overridden: false, resets_at: null },
  comments_per_channel_per_day: {
    limit: 3,
    used: 0,
    fleet_default: 3,
    overridden: false,
    resets_at: null,
  },
  busiest_channel: null,
};

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(ui, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
}

beforeEach(() => {
  // The suite-wide setup stubs fetch as a vi.fn and resets it after each test, so the
  // response is set per test rather than by stubbing the global again.
  vi.mocked(fetch).mockImplementation(
    () =>
      Promise.resolve(
        new Response(JSON.stringify(LIMITS), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ) as Promise<Response>,
  );
});

const ACCOUNTS: NeuroAccountRow[] = [
  { account_id: 'a1', name: 'Vika Ix', linked: true, pinned_channels: ['@crypto'] },
  { account_id: 'a2', name: '+79990000002', linked: false, pinned_channels: [] },
];
const CHANNELS = ['@crypto', '@news'];

test('assigns an idle account, confirms removal, and closes', async () => {
  const onClose = vi.fn();
  const onPick = vi.fn();
  const onRemove = vi.fn();
  renderWithClient(
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
  // the channel list belongs to the linked account only — one per linked row, never for
  // an idle one (the list is a sibling of the row's top line, not part of the trigger).
  expect(screen.getAllByRole('listbox')).toHaveLength(1);

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
  renderWithClient(
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
  renderWithClient(
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

// The channel list of every linked row is rendered whether it is open or not (the
// .tb-dd class only collapses it visually), so `inert` is what keeps a keyboard
// operator from tabbing through the options of every collapsed row on the page.
test('a collapsed channel list takes no focus, an expanded one does', async () => {
  renderWithClient(
    <NeuroAccountsModal
      accounts={ACCOUNTS}
      channels={CHANNELS}
      onClose={vi.fn()}
      onPick={vi.fn()}
      onRemove={vi.fn()}
      onChannelChange={vi.fn()}
    />,
  );
  const collapsed = screen.getByRole('option', { name: '@news' });
  collapsed.focus();
  expect(collapsed).not.toHaveFocus();

  await userEvent.click(screen.getByLabelText('Каналы аккаунта'));
  const expanded = screen.getByRole('option', { name: '@news' });
  expanded.focus();
  expect(expanded).toHaveFocus();
});

test('a multi-channel subset shows a count in the trigger', () => {
  renderWithClient(
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
  renderWithClient(
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

test('a t.me channel is labelled by the part that tells it apart', async () => {
  // Channels entered as full links share their first 13 characters, so a truncating
  // label used to keep only "https://t.me/…" — the identical half. Full link on hover.
  renderWithClient(
    <NeuroAccountsModal
      accounts={[
        {
          account_id: 'a4',
          name: 'Marina',
          linked: true,
          pinned_channels: ['https://t.me/iris_shop'],
        },
      ]}
      channels={['https://t.me/laqueshia', 'https://t.me/iris_shop']}
      onClose={vi.fn()}
      onPick={vi.fn()}
      onRemove={vi.fn()}
      onChannelChange={vi.fn()}
    />,
  );
  const trigger = screen.getByLabelText('Каналы аккаунта');
  expect(trigger).toHaveTextContent('iris_shop');
  expect(trigger).not.toHaveTextContent('t.me');

  await userEvent.click(trigger);
  expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
    'Все каналы',
    'laqueshia',
    'iris_shop',
  ]);
  expect(screen.getByRole('option', { name: 'iris_shop' })).toHaveAttribute(
    'title',
    'https://t.me/iris_shop',
  );
});

test('empty list shows the empty hint', () => {
  renderWithClient(
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
  const { rerender } = renderWithClient(
    <NeuroAccountsModal
      accounts={ACCOUNTS}
      onClose={vi.fn()}
      onPick={vi.fn()}
      onRemove={vi.fn()}
      onChannelChange={vi.fn()}
      feedback={{ a1: 'ok' }}
    />,
  );
  expect(document.querySelector('.text-success-deep svg')).toBeInTheDocument();

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

test('names the channels an account is banned in for good', () => {
  // A per-pair ban is permanent and the channel row hides it (a sibling account still
  // posts there), so this modal is the only place the operator learns WHO is burnt
  // WHERE — right next to the button that adds a replacement account.
  renderWithClient(
    <NeuroAccountsModal
      accounts={[
        { ...ACCOUNTS[0]!, banned_channels: ['https://t.me/news', '@crypto'] },
        ACCOUNTS[1]!,
      ]}
      channels={CHANNELS}
      onClose={vi.fn()}
      onPick={vi.fn()}
      onRemove={vi.fn()}
      onChannelChange={vi.fn()}
    />,
  );
  expect(screen.getByText('Забанен навсегда: news, @crypto')).toBeInTheDocument();
});

test('the limits chip reports the tightest cap and opens the limits modal', async () => {
  // The chip is the at-a-glance: without opening anything the operator sees which cap is
  // closest to binding — here 8 of 20 joins, the highest share of the three. Only the
  // LINKED row gets one: the unlinked rows are the whole warmed fleet.
  renderWithClient(
    <NeuroAccountsModal
      accounts={ACCOUNTS}
      channels={CHANNELS}
      onClose={vi.fn()}
      onPick={vi.fn()}
      onRemove={vi.fn()}
      onChannelChange={vi.fn()}
    />,
  );
  // The accessible name is the spend, not the word — the chip exists to say `8/20` out
  // loud, so it is found by its title instead.
  const chips = await screen.findAllByTitle('Лимиты');
  await waitFor(() => {
    expect(chips[0]).toHaveTextContent('8/20');
  });

  expect(chips).toHaveLength(1);

  await userEvent.click(chips[0]!);

  expect(await screen.findByText('Лимиты · Vika Ix')).toBeInTheDocument();
  expect(screen.getByLabelText('Вступления в каналы')).toHaveValue(null);
});
