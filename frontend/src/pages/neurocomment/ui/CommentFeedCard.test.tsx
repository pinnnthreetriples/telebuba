import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { CommentRecord, NeurocommentAccountCard } from '@/shared/api';

import { CommentFeedCard } from './CommentFeedCard';

const ACCOUNTS: NeurocommentAccountCard[] = [
  {
    account_id: 'acc-1',
    label: 'Account One',
    health: 'ready',
    trust_score: 80,
    trust_band: 'high',
    comments_last_hour: 1,
    max_comments_per_hour: 10,
    comments_today: 2,
  },
];

function comment(post_id: number, text: string): CommentRecord {
  return {
    channel: '@chan',
    post_id,
    campaign_id: 'c1',
    account_id: 'acc-1',
    status: 'posted',
    comment_text: text,
    created_at: '2026-07-11T10:00:00+00:00',
    updated_at: '2026-07-11T10:00:00+00:00',
  };
}

test('renders one row per comment with its resolved account label', () => {
  render(
    <CommentFeedCard
      comments={[comment(2, 'second'), comment(1, 'first')]}
      accounts={ACCOUNTS}
      onOpenHistory={vi.fn()}
    />,
  );
  expect(screen.getByText('second')).toBeInTheDocument();
  expect(screen.getByText('first')).toBeInTheDocument();
  // account_id is resolved to the human label, shown once per row
  expect(screen.getAllByText('Account One')).toHaveLength(2);
});

test('shows the empty state when there are no comments', () => {
  render(<CommentFeedCard comments={[]} accounts={ACCOUNTS} onOpenHistory={vi.fn()} />);
  expect(screen.getByText('Пока нет опубликованных комментариев')).toBeInTheDocument();
});

test('the history button fires onOpenHistory', async () => {
  const onOpenHistory = vi.fn();
  render(<CommentFeedCard comments={[]} accounts={ACCOUNTS} onOpenHistory={onOpenHistory} />);
  await userEvent.click(screen.getByRole('button', { name: 'Вся история' }));
  expect(onOpenHistory).toHaveBeenCalledOnce();
});
