import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { NeurocommentBoard as NeurocommentBoardData } from '@/shared/api';

import { NeurocommentBoard } from './NeurocommentBoard';

// Default resolver = the old behaviour (show the label), so every existing
// assertion below still describes what the board renders.
const LABEL = (_accountId: string, fallback: string): string => fallback;

const BOARD: NeurocommentBoardData = {
  campaign_id: 'c1',
  campaign_name: 'Promo',
  status: 'active',
  channels: [{ channel: '@news', status: 'ready', ready_accounts: 2, total_accounts: 3 }],
  accounts: [
    {
      account_id: 'acc-1',
      label: '+79261112233',
      comments_last_hour: 1,
      max_comments_per_hour: 10,
      comments_today: 4,
      last_comment_at: 'now',
      last_comment_text: 'Отличный пост!',
      readiness: [{ channel: '@news', ready: true, joined: true, captcha_passed: true }],
    },
    {
      account_id: 'acc-2',
      label: '+15550000000',
      comments_last_hour: 0,
      max_comments_per_hour: 10,
      comments_today: 0,
      readiness: [],
    },
  ],
};

test('renders the 4-column work table with channel and dot-pill status', () => {
  render(
    <NeurocommentBoard
      board={BOARD}
      accountsCount={1}
      onOpenAccounts={() => undefined}
      displayName={LABEL}
    />,
  );
  expect(screen.getByText('+79261112233')).toBeInTheDocument();
  expect(screen.getByText('@news')).toBeInTheDocument();
  expect(screen.getByText('Готов')).toBeInTheDocument();
  // the real last-comment text is shown (was a generic placeholder)
  expect(screen.getByText('Отличный пост!')).toBeInTheDocument();
});

test('shows a deleted-count chip on a channel with recent deletions', () => {
  const board: NeurocommentBoardData = {
    ...BOARD,
    channels: [
      {
        channel: '@news',
        status: 'ready',
        ready_accounts: 2,
        total_accounts: 3,
        deleted_recent: 3,
      },
    ],
  };
  render(
    <NeurocommentBoard
      board={board}
      accountsCount={1}
      onOpenAccounts={() => undefined}
      displayName={LABEL}
    />,
  );
  expect(screen.getByText('3 удалено')).toBeInTheDocument();
});

test('an account with no readiness rows shows the no-data badge, not comments-off', () => {
  render(
    <NeurocommentBoard
      board={BOARD}
      accountsCount={1}
      onOpenAccounts={() => undefined}
      displayName={LABEL}
    />,
  );
  // acc-2 has readiness: [] — no channel to look up, so the frontend-only
  // 'no_data' status renders instead of colliding with the real backend state.
  expect(screen.getByText('Нет данных')).toBeInTheDocument();
  expect(screen.queryByText('Комментарии выкл.')).not.toBeInTheDocument();
});

test('a pinned account shows its pinned channel, not the first joined one', () => {
  const board: NeurocommentBoardData = {
    ...BOARD,
    channels: [
      { channel: '@news', status: 'ready', ready_accounts: 1, total_accounts: 1 },
      { channel: '@second', status: 'throttled', ready_accounts: 0, total_accounts: 1 },
    ],
    accounts: [
      {
        ...BOARD.accounts![0]!,
        pinned_channels: ['@second'],
        readiness: [
          { channel: '@news', ready: true, joined: true, captcha_passed: true },
          { channel: '@second', ready: false, joined: false, captcha_passed: false },
        ],
      },
    ],
  };
  render(
    <NeurocommentBoard
      board={board}
      accountsCount={1}
      onOpenAccounts={() => undefined}
      displayName={LABEL}
    />,
  );
  expect(screen.getByText('@second')).toBeInTheDocument();
  expect(screen.queryByText('@news')).not.toBeInTheDocument();
});

test('an account banned in its row’s channel does not inherit the channel’s healthy status', () => {
  // The ban is per (account, channel) and permanent — no un-ban, no retry. The other
  // five accounts still post in @news, so the CHANNEL aggregate is 'ready'; reading that
  // aggregate into this account's row told the operator a burnt pair was fine, while the
  // only remedy (add another account) was being suggested elsewhere.
  const board: NeurocommentBoardData = {
    ...BOARD,
    channels: [{ channel: '@news', status: 'ready', ready_accounts: 5, total_accounts: 6 }],
    accounts: [
      {
        ...BOARD.accounts![0]!,
        readiness: [
          { channel: '@news', ready: false, joined: false, captcha_passed: true, banned: true },
        ],
      },
    ],
  };
  render(
    <NeurocommentBoard
      board={board}
      accountsCount={1}
      onOpenAccounts={() => undefined}
      displayName={LABEL}
    />,
  );
  expect(screen.queryByText('Готов')).not.toBeInTheDocument();
  // The permanent reading, not the temporary "Возвращаемся в чат" one.
  expect(screen.getByText('Забанен')).toBeInTheDocument();
  expect(screen.queryByText('Возвращаемся в чат')).not.toBeInTheDocument();
});

test('an account out of re-join attempts is shown as such, not as the channel’s status', () => {
  // Same hole as the ban above, and the same fix: the channel keeps working on its other
  // accounts, so its aggregate stays 'ready' while THIS account has left the chat for
  // good. The row is also picked for that channel over the one it still works in —
  // "@working is fine" is already in the N/M badge, "@news is lost" was nowhere.
  const board: NeurocommentBoardData = {
    ...BOARD,
    channels: [
      { channel: '@news', status: 'ready', ready_accounts: 5, total_accounts: 6 },
      { channel: '@working', status: 'ready', ready_accounts: 6, total_accounts: 6 },
    ],
    accounts: [
      {
        ...BOARD.accounts![0]!,
        readiness: [
          { channel: '@working', ready: true, joined: true, captcha_passed: true },
          {
            channel: '@news',
            ready: false,
            joined: false,
            captcha_passed: true,
            rejoin_gave_up: true,
          },
        ],
      },
    ],
  };
  render(
    <NeurocommentBoard
      board={board}
      accountsCount={1}
      onOpenAccounts={() => undefined}
      displayName={LABEL}
    />,
  );
  expect(screen.getByText('@news')).toBeInTheDocument();
  expect(screen.getByText('Попытки входа исчерпаны')).toBeInTheDocument();
  expect(screen.queryByText('Готов')).not.toBeInTheDocument();
});

test('a pair back in the chat is not badged as exhausted, whatever its old mark says', () => {
  // The mark says "this budget was spent and reported", not "this pair is out". A pair
  // that got back in carries it until the write that clears it lands (a real re-join, a
  // re-link), and painting a working pair red — or picking its row over the healthy one —
  // would report a problem the operator cannot act on.
  const board: NeurocommentBoardData = {
    ...BOARD,
    channels: [{ channel: '@news', status: 'ready', ready_accounts: 6, total_accounts: 6 }],
    accounts: [
      {
        ...BOARD.accounts![0]!,
        readiness: [
          {
            channel: '@news',
            ready: true,
            joined: true,
            captcha_passed: true,
            rejoin_gave_up: true,
          },
        ],
      },
    ],
  };
  render(
    <NeurocommentBoard
      board={board}
      accountsCount={1}
      onOpenAccounts={() => undefined}
      displayName={LABEL}
    />,
  );
  expect(screen.getByText('Готов')).toBeInTheDocument();
  expect(screen.queryByText('Попытки входа исчерпаны')).not.toBeInTheDocument();
});

test('during onboarding, a not-yet-armed account animates progress instead of "no data"', () => {
  render(
    <NeurocommentBoard
      board={BOARD}
      accountsCount={1}
      onboarding
      onOpenAccounts={() => undefined}
      displayName={LABEL}
    />,
  );
  // header carries the live onboarding indicator (was a static "updated" label)
  expect(screen.getByText('Подключаем аккаунты')).toBeInTheDocument();
  // acc-2 has 0 of 1 channels ready → animated progress, not the misleading no-data
  expect(screen.getByText('Подключаем аккаунты 0/1')).toBeInTheDocument();
  expect(screen.queryByText('Нет данных')).not.toBeInTheDocument();
  // acc-1 is fully armed (1/1) → keeps its real status even mid-onboarding
  expect(screen.getByText('Готов')).toBeInTheDocument();
});

test('with onboarding off, the static status shows (no progress badge)', () => {
  render(
    <NeurocommentBoard
      board={BOARD}
      accountsCount={1}
      onOpenAccounts={() => undefined}
      displayName={LABEL}
    />,
  );
  expect(screen.queryByText('Подключаем аккаунты')).not.toBeInTheDocument();
  expect(screen.queryByText('Подключаем аккаунты 0/1')).not.toBeInTheDocument();
  expect(screen.getByText('Нет данных')).toBeInTheDocument();
});

test('the gear button opens the accounts modal', async () => {
  const onOpenAccounts = vi.fn();
  render(
    <NeurocommentBoard
      board={BOARD}
      accountsCount={1}
      onOpenAccounts={onOpenAccounts}
      displayName={LABEL}
    />,
  );
  await userEvent.click(screen.getByLabelText('Аккаунты в нейрокомментинге'));
  expect(onOpenAccounts).toHaveBeenCalledOnce();
});

test('expanding an account row reveals only that account’s published comments', async () => {
  const board: NeurocommentBoardData = {
    ...BOARD,
    comments: [
      {
        channel: '@news',
        post_id: 1,
        campaign_id: 'c1',
        account_id: 'acc-1',
        status: 'posted',
        comment_text: 'mine',
        created_at: '2026-07-11T10:00:00+00:00',
        updated_at: '2026-07-11T10:00:00+00:00',
      },
      {
        channel: '@news',
        post_id: 2,
        campaign_id: 'c1',
        account_id: 'acc-2',
        status: 'posted',
        comment_text: 'theirs',
        created_at: '2026-07-11T10:00:00+00:00',
        updated_at: '2026-07-11T10:00:00+00:00',
      },
    ],
  };
  const onOpenHistory = vi.fn();
  render(
    <NeurocommentBoard
      board={board}
      accountsCount={1}
      onOpenAccounts={() => undefined}
      onOpenHistory={onOpenHistory}
      displayName={LABEL}
    />,
  );
  // collapsed by default — neither comment is visible yet
  expect(screen.queryByText('mine')).not.toBeInTheDocument();
  // the first account row's expander is the first "Опубликованные комментарии" button
  const expanders = screen.getAllByRole('button', { name: 'Опубликованные комментарии' });
  await userEvent.click(expanders[0]!);
  // only acc-1's comment shows, not acc-2's
  expect(screen.getByText('mine')).toBeInTheDocument();
  expect(screen.queryByText('theirs')).not.toBeInTheDocument();
  // and the history button reaches the modal opener
  await userEvent.click(screen.getByRole('button', { name: 'Вся история' }));
  expect(onOpenHistory).toHaveBeenCalledOnce();
});

test('the open board body carries no max-height cap to clip an expanded account', () => {
  // `.tb-collapse.tb-open` caps the body at `var(--mh, 600px)` with overflow:hidden.
  // The board used to hand-roll that class and never lift the cap, so on a phone —
  // where six account cards are already past 600px — expanding the last account
  // revealed a sub-row nobody could see. `.tb-settled` is what drops the cap.
  const { container } = render(
    <NeurocommentBoard
      board={BOARD}
      accountsCount={1}
      onOpenAccounts={() => undefined}
      displayName={LABEL}
    />,
  );

  const body = container.querySelector('.tb-collapse');
  expect(body).toHaveClass('tb-open', 'tb-settled');
});

// One account armed in two channels, having last commented in the second. The channel
// comes off the CARD (`last_comment_channel`) and not out of `board.comments`: that feed
// is a campaign-wide newest-first prefix capped at 50, so a busy account falls out of it
// within the hour and the row would pair its real comment text with a channel it merely
// joined — the very mismatch this column exists to prevent.
function twoChannelBoard(
  account: Partial<NonNullable<NeurocommentBoardData['accounts']>[number]>,
): NeurocommentBoardData {
  return {
    ...BOARD,
    channels: [
      { channel: '@news', status: 'ready', ready_accounts: 2, total_accounts: 3 },
      { channel: '@sport', status: 'ready', ready_accounts: 1, total_accounts: 3 },
    ],
    accounts: [
      {
        ...BOARD.accounts![0]!,
        readiness: [
          { channel: '@news', ready: true, joined: true, captcha_passed: true },
          { channel: '@sport', ready: true, joined: true, captcha_passed: true },
        ],
        ...account,
      },
    ],
  };
}

function renderBoard(board: NeurocommentBoardData) {
  render(
    <NeurocommentBoard
      board={board}
      accountsCount={1}
      onOpenAccounts={() => undefined}
      displayName={LABEL}
    />,
  );
}

// The row exists to name the comment it shows. It used to stand still on the first
// joined channel for the whole campaign, so the operator could not tell from the board
// where an account was actually working.
test('the channel column follows the account’s last comment', () => {
  renderBoard(twoChannelBoard({ last_comment_channel: '@sport' }));

  expect(screen.getByText('@sport')).toBeInTheDocument();
  expect(screen.queryByText('@news')).not.toBeInTheDocument();
});

// The comment feed is capped campaign-wide, so it can lack this account entirely while
// the card still knows where it posted. Reading the feed instead silently reverted the
// row to the readiness pick for exactly the busiest accounts.
test('the channel column ignores the capped comment feed and reads the card', () => {
  const board = twoChannelBoard({ last_comment_channel: '@sport' });
  renderBoard({ ...board, comments: [] });

  expect(screen.getByText('@sport')).toBeInTheDocument();
});

// A pin is the operator instructing this account where to work. Re-pinning has to show
// up straight away — waiting for the account's next comment can mean waiting a day.
test('a pin outranks the last-commented channel', () => {
  renderBoard(twoChannelBoard({ last_comment_channel: '@sport', pinned_channels: ['@news'] }));

  expect(screen.getByText('@news')).toBeInTheDocument();
  expect(screen.queryByText('@sport')).not.toBeInTheDocument();
});

// With several pins the operator has named a set, not one channel, so within that set the
// live one wins again.
test('among several pins the last-commented one wins', () => {
  renderBoard(
    twoChannelBoard({ last_comment_channel: '@sport', pinned_channels: ['@news', '@sport'] }),
  );

  expect(screen.getByText('@sport')).toBeInTheDocument();
});

// Accepted trade-off, stated so a future reader does not "fix" it by accident: the live
// channel outranks a stuck pair, so a ban stops surfacing HERE once the account posts
// elsewhere. `banned_channels` in the neuro-accounts modal is where it still shows.
test('the last-commented channel outranks a pair banned elsewhere', () => {
  renderBoard(
    twoChannelBoard({
      last_comment_channel: '@sport',
      readiness: [
        { channel: '@news', ready: false, joined: false, captcha_passed: false, banned: true },
        { channel: '@sport', ready: true, joined: true, captcha_passed: true },
      ],
    }),
  );

  expect(screen.getByText('@sport')).toBeInTheDocument();
  expect(screen.queryByText('Забанен')).not.toBeInTheDocument();
});

test('shows the Telegram name, not the raw session id, in the account column', () => {
  // Reproduces the live data: an imported session has an empty operator label, so
  // the backend board sends the session-stem id and the column read "5_telethon".
  const board: NeurocommentBoardData = {
    ...BOARD,
    accounts: [
      {
        account_id: '5_telethon',
        label: '5_telethon',
        comments_last_hour: 0,
        max_comments_per_hour: 10,
        comments_today: 0,
        readiness: [{ channel: '@news', ready: true, joined: true, captcha_passed: true }],
      },
    ],
  };
  const names: Record<string, string> = { '5_telethon': 'Alisa' };

  render(
    <NeurocommentBoard
      board={board}
      accountsCount={1}
      onOpenAccounts={() => undefined}
      displayName={(id, fallback) => names[id] ?? fallback}
    />,
  );

  expect(screen.getByText('Alisa')).toBeInTheDocument();
  expect(screen.queryByText('5_telethon')).not.toBeInTheDocument();
});

test('falls back to the label when the account is not in the full list', () => {
  render(
    <NeurocommentBoard
      board={BOARD}
      accountsCount={1}
      onOpenAccounts={() => undefined}
      displayName={(_id, fallback) => fallback}
    />,
  );

  expect(screen.getByText('+79261112233')).toBeInTheDocument();
});
