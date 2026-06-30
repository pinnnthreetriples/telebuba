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

test('the gear button opens the accounts modal', async () => {
  const onOpenAccounts = vi.fn();
  render(<NeurocommentBoard board={BOARD} accountsCount={1} onOpenAccounts={onOpenAccounts} />);
  await userEvent.click(screen.getByLabelText('Аккаунты в нейрокомментинге'));
  expect(onOpenAccounts).toHaveBeenCalledOnce();
});
