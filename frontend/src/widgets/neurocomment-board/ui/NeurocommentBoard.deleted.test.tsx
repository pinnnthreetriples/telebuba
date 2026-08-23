import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import type { NeurocommentBoard as NeurocommentBoardData } from '@/shared/api';

import { NeurocommentBoard } from './NeurocommentBoard';

// Everything the board row says about a REMOVED comment: the per-(account, channel)
// deletion chip beside the channel name, and the strike-through on the comment itself.
// Split from `NeurocommentBoard.test.tsx` at the 700-line test cap — the two are one
// behaviour, because the whole point of the strike is to explain the chip next to it.

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

test('the deleted-count chip belongs to the account that lost the comment, not the channel', () => {
  // Both accounts sit on @news and the CHANNEL carries 3 deletions, but only acc-1 has
  // any of its own. Reading the channel aggregate stamped '3 удалено' onto both rows —
  // acc-2 was accused of a deletion for a comment it never posted.
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
    accounts: [
      {
        ...BOARD.accounts![0]!,
        deleted_today: 1,
        readiness: [
          { channel: '@news', ready: true, joined: true, captcha_passed: true, deleted: 1 },
        ],
      },
      {
        ...BOARD.accounts![1]!,
        deleted_today: 0,
        readiness: [{ channel: '@news', ready: true, joined: true, captcha_passed: true }],
      },
    ],
  };
  render(
    <NeurocommentBoard
      board={board}
      accountsCount={2}
      onOpenAccounts={() => undefined}
      displayName={LABEL}
    />,
  );
  expect(screen.getAllByText('@news')).toHaveLength(2);
  expect(screen.getAllByText('1 удалено')).toHaveLength(1);
  expect(screen.queryByText('3 удалено')).not.toBeInTheDocument();
});

test('the chip counts only the channel the row names, not the account total', () => {
  // acc-1 lost one comment in @old and none in @news; the row shows @news because that is
  // where it commented last. The flat `deleted_today: 1` would have hung the chip on
  // @news and accused a channel where nothing was deleted.
  const board: NeurocommentBoardData = {
    ...BOARD,
    channels: [
      { channel: '@news', status: 'ready', ready_accounts: 1, total_accounts: 1 },
      { channel: '@old', status: 'ready', ready_accounts: 1, total_accounts: 1 },
    ],
    accounts: [
      {
        ...BOARD.accounts![0]!,
        last_comment_channel: '@news',
        deleted_today: 1,
        readiness: [
          { channel: '@news', ready: true, joined: true, captcha_passed: true },
          { channel: '@old', ready: true, joined: true, captcha_passed: true, deleted: 1 },
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
  expect(screen.queryByText('1 удалено')).not.toBeInTheDocument();
});

test('the chip shows for the pinned channel the row displays, not the last-commented one', () => {
  // The positive half of the pair above, and the case the operator actually reads: a pin
  // outranks the last-comment channel, so the row names @old while the account's newest
  // comment went to @news. The chip must follow the NAMED channel — which is the one that
  // lost comments here. A negative-only pair of tests would pass on a hardcoded 0.
  const board: NeurocommentBoardData = {
    ...BOARD,
    channels: [
      { channel: '@news', status: 'ready', ready_accounts: 1, total_accounts: 1 },
      { channel: '@old', status: 'ready', ready_accounts: 1, total_accounts: 1 },
    ],
    accounts: [
      {
        ...BOARD.accounts![0]!,
        pinned_channels: ['@old'],
        last_comment_channel: '@news',
        deleted_today: 2,
        readiness: [
          { channel: '@news', ready: true, joined: true, captcha_passed: true },
          { channel: '@old', ready: true, joined: true, captcha_passed: true, deleted: 2 },
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
  expect(screen.getByText('@old')).toBeInTheDocument();
  expect(screen.getByText('2 удалено')).toBeInTheDocument();
});

test('an account with no channel gets no deleted chip beside the em dash', () => {
  // The row resolved no readiness row at all, so there is no pair to read a count off and
  // the '—' placeholder cannot inherit one from the account's flat total.
  const board: NeurocommentBoardData = {
    ...BOARD,
    accounts: [{ ...BOARD.accounts![1]!, deleted_today: 3 }],
  };
  render(
    <NeurocommentBoard
      board={board}
      accountsCount={1}
      onOpenAccounts={() => undefined}
      displayName={LABEL}
    />,
  );
  // Anchored on the channel cell specifically: the comment cell also renders '—' for an
  // account that never posted, so a bare `getAllByText('—')` would pass without the
  // channel column having rendered anything at all.
  const channelCell = screen.getByText('—', { selector: 'span.tb-swapin' });
  expect(channelCell.textContent).toBe('—');
  expect(screen.queryByText('3 удалено')).not.toBeInTheDocument();
});

test('the row strikes through the last comment when the sweep found it gone', () => {
  // The expanded feed below the row already marks a deleted comment; the row itself read
  // as if the comment were still live, so the operator saw a working account with a
  // deletion chip and no way to connect the two. Coherent fixture: the comment, the
  // channel the row names and the chip all describe @news.
  const board: NeurocommentBoardData = {
    ...BOARD,
    accounts: [
      {
        ...BOARD.accounts![0]!,
        last_comment_channel: '@news',
        last_comment_deleted: true,
        readiness: [
          { channel: '@news', ready: true, joined: true, captcha_passed: true, deleted: 1 },
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
  const text = screen.getByText('Отличный пост!');
  expect(text).toHaveClass('line-through', 'text-danger');
  expect(text).toHaveAttribute('title', 'Этот комментарий удалён в канале @news');
  expect(screen.getByText('1 удалено')).toBeInTheDocument();
});

test('the strike names the channel the comment went to, not the one the row displays', () => {
  // A pin outranks the last-comment channel, so the row names @news while the struck
  // comment was made — and removed — in @old, where the chip is silent. Without the
  // channel in the hover text the strike reads as an accusation against @news, which the
  // row simultaneously reports as having lost nothing.
  const board: NeurocommentBoardData = {
    ...BOARD,
    channels: [
      { channel: '@news', status: 'ready', ready_accounts: 1, total_accounts: 1 },
      { channel: '@old', status: 'ready', ready_accounts: 1, total_accounts: 1 },
    ],
    accounts: [
      {
        ...BOARD.accounts![0]!,
        pinned_channels: ['@news'],
        last_comment_channel: '@old',
        last_comment_deleted: true,
        readiness: [
          { channel: '@news', ready: true, joined: true, captcha_passed: true },
          { channel: '@old', ready: true, joined: true, captcha_passed: true, deleted: 1 },
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
  expect(screen.getByText('Отличный пост!')).toHaveAttribute(
    'title',
    'Этот комментарий удалён в канале @old',
  );
});

test('the strike is suppressed when there is no comment text to strike', () => {
  // `last_comment_deleted` rides the card, the text does not have to: a card can carry the
  // flag with a null text, and the cell then shows the "Комментарий отправлен" stand-in.
  // Striking a placeholder marks a comment the row is not showing.
  const board: NeurocommentBoardData = {
    ...BOARD,
    accounts: [
      {
        ...BOARD.accounts![0]!,
        last_comment_text: undefined,
        last_comment_at: 'now',
        last_comment_deleted: true,
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
  expect(screen.getByText('Комментарий отправлен')).not.toHaveClass('line-through');
});

test('a live last comment is not struck through', () => {
  render(
    <NeurocommentBoard
      board={BOARD}
      accountsCount={1}
      onOpenAccounts={() => undefined}
      displayName={LABEL}
    />,
  );
  expect(screen.getByText('Отличный пост!')).not.toHaveClass('line-through');
});
