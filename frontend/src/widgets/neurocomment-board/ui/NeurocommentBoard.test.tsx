import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

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
      label: 'Main',
      health: 'ok',
      trust_score: 90,
      trust_band: 'excellent',
      comments_last_hour: 1,
      max_comments_per_hour: 10,
      comments_today: 4,
    },
  ],
};

test('renders channel rows with status badges and account cards', () => {
  render(<NeurocommentBoard board={BOARD} />);
  expect(screen.getByText('@news')).toBeInTheDocument();
  expect(screen.getByText('Готов')).toBeInTheDocument();
  expect(screen.getByText('acc-1')).toBeInTheDocument();
  expect(screen.getByText('2/3')).toBeInTheDocument();
});
