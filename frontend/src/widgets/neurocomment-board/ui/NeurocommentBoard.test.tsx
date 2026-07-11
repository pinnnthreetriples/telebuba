import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { NeurocommentBoard as NeurocommentBoardData } from '@/shared/api';

import { NeurocommentBoard } from './NeurocommentBoard';

const BOARD: NeurocommentBoardData = {
  campaign_id: 'c1',
  campaign_name: 'Promo',
  status: 'active',
  channels: [{ channel: '@news', status: 'ready', ready_accounts: 2, total_accounts: 3 }],
  accounts: [
    {
      account_id: 'acc-1',
      label: '+79261112233',
      health: 'ok',
      trust_score: 90,
      trust_band: 'excellent',
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
      health: 'blocked',
      trust_score: 30,
      trust_band: 'at_risk',
      comments_last_hour: 0,
      max_comments_per_hour: 10,
      comments_today: 0,
      readiness: [],
    },
  ],
};

test('renders the 4-column work table with channel and dot-pill status', () => {
  render(<NeurocommentBoard board={BOARD} accountsCount={1} onOpenAccounts={() => undefined} />);
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
  render(<NeurocommentBoard board={board} accountsCount={1} onOpenAccounts={() => undefined} />);
  expect(screen.getByText('3 удалено')).toBeInTheDocument();
});

test('an account with no readiness rows shows the no-data badge, not comments-off', () => {
  render(<NeurocommentBoard board={BOARD} accountsCount={1} onOpenAccounts={() => undefined} />);
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
        pinned_channel: '@second',
        readiness: [
          { channel: '@news', ready: true, joined: true, captcha_passed: true },
          { channel: '@second', ready: false, joined: false, captcha_passed: false },
        ],
      },
    ],
  };
  render(<NeurocommentBoard board={board} accountsCount={1} onOpenAccounts={() => undefined} />);
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
  render(<NeurocommentBoard board={BOARD} accountsCount={1} onOpenAccounts={() => undefined} />);
  expect(screen.queryByText('Онбординг идёт')).not.toBeInTheDocument();
  expect(screen.queryByText('Онбординг 0/1')).not.toBeInTheDocument();
  expect(screen.getByText('Нет данных')).toBeInTheDocument();
});

test('the gear button opens the accounts modal', async () => {
  const onOpenAccounts = vi.fn();
  render(<NeurocommentBoard board={BOARD} accountsCount={1} onOpenAccounts={onOpenAccounts} />);
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
