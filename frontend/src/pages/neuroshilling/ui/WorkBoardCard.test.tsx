import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { NeuroshillingCampaign } from '@/shared/api';

import { WorkBoardCard } from './WorkBoardCard';

function campaign(over: Partial<NeuroshillingCampaign> = {}): NeuroshillingCampaign {
  return {
    campaign_id: 'c1',
    name: 'Промо',
    mode: 'campaign',
    status: 'idle',
    created_at: 'now',
    updated_at: 'now',
    ...over,
  };
}

const LIST = [
  campaign({ campaign_id: 'c1', name: 'Промо', topic: 'табак', targets_raw: '@a @b' }),
  campaign({ campaign_id: 'c2', name: 'Ревайв', status: 'running', targets_raw: '@x @y @z' }),
];

function renderCard(over: Partial<Parameters<typeof WorkBoardCard>[0]> = {}) {
  const onSelect = vi.fn();
  render(
    <WorkBoardCard
      campaignList={LIST}
      campaignId="c1"
      run={{ status: 'idle', sent: 3, total: 8 }}
      targets={['@a', '@b']}
      onSelect={onSelect}
      {...over}
    />,
  );
  return { onSelect };
}

test('every campaign gets a row, not only the selected one', () => {
  // The whole point of the board: «what is going on» used to be readable one
  // campaign at a time, by switching between them.
  renderCard();
  expect(screen.getByText('Промо')).toBeInTheDocument();
  expect(screen.getByText('Ревайв')).toBeInTheDocument();
});

test('progress is shown for the campaign whose run we actually read, and dashed elsewhere', () => {
  // `sent`/`total` come from the board of ONE campaign. A zero on the other rows
  // would claim «nothing sent», which is not what we know about them.
  renderCard();
  expect(screen.getByText('3/8')).toBeInTheDocument();
  expect(screen.getByText('—')).toBeInTheDocument();
});

test('the selected row counts targets the way the server parsed them', () => {
  // The pipeline card stands on the same page counting the server's list. Two
  // different numbers for one campaign on one screen is the defect this avoids.
  // Единицу называет заголовок столбца, поэтому в ячейке — число.
  renderCard({ targets: ['@a'] });
  expect(screen.getByText('1')).toBeInTheDocument();
  // The unselected row has no server parse to use, so it falls back to the client
  // count of what was saved.
  expect(screen.getByText('3')).toBeInTheDocument();
});

test('a row click selects its campaign', async () => {
  const { onSelect } = renderCard();
  await userEvent.click(screen.getByText('Ревайв'));
  expect(onSelect).toHaveBeenCalledWith('c2');
});

test('the header counts the campaigns and how many of them are running', () => {
  renderCard();
  expect(screen.getByText('В работе: 1')).toBeInTheDocument();
});
