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
  expect(screen.getByText('Онбординг идёт')).toBeInTheDocument();
  // acc-2 has 0 of 1 channels ready → animated progress, not the misleading no-data
  expect(screen.getByText('Онбординг 0/1')).toBeInTheDocument();
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
  expect(screen.queryByText('Онбординг идёт')).not.toBeInTheDocument();
  expect(screen.queryByText('Онбординг 0/1')).not.toBeInTheDocument();
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
