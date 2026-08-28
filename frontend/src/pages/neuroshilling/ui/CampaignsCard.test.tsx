import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { NeuroshillingCampaign } from '@/shared/api';

import { CampaignsCard } from './CampaignsCard';

const CAMPAIGN: NeuroshillingCampaign = {
  campaign_id: 'c1',
  name: 'Промо',
  mode: 'campaign',
  status: 'idle',
  created_at: 'now',
  updated_at: 'now',
};

function renderCard(props: Partial<Parameters<typeof CampaignsCard>[0]> = {}) {
  const handlers = {
    onSelect: vi.fn(),
    onDelete: vi.fn(),
    onStartCreate: vi.fn(),
    onCancelCreate: vi.fn(),
    onCreateName: vi.fn(),
    onCreate: vi.fn(),
  };
  render(
    <CampaignsCard
      campaignList={[CAMPAIGN]}
      campaignId="c1"
      creating={false}
      createName=""
      {...handlers}
      {...props}
    />,
  );
  return handlers;
}

test('a row selects on click and its delete button does not select', async () => {
  const handlers = renderCard();
  expect(screen.getByText('Не запущена')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Промо'));
  expect(handlers.onSelect).toHaveBeenCalledWith('c1');

  // The delete button sits inside the row surface: without stopPropagation it
  // would select the campaign on the way to opening the confirm.
  await userEvent.click(screen.getByLabelText('Удалить кампанию'));
  expect(handlers.onDelete).toHaveBeenCalledWith(CAMPAIGN);
  expect(handlers.onSelect).toHaveBeenCalledTimes(1);
});

test('the selected row is the only one carrying the selected border', () => {
  renderCard({
    campaignList: [CAMPAIGN, { ...CAMPAIGN, campaign_id: 'c2', name: 'Вторая', status: 'running' }],
  });

  const selected = screen.getByText('Промо').closest('[role="button"]');
  const other = screen.getByText('Вторая').closest('[role="button"]');
  expect(selected?.className).toContain('border-action-primary');
  expect(other?.className).not.toContain('border-action-primary');
  expect(screen.getByText('Работает')).toBeInTheDocument();
});

test('with no campaigns it shows the empty state and still offers create', async () => {
  const handlers = renderCard({ campaignList: [], campaignId: null });

  expect(screen.getByText('Пока нет кампаний')).toBeInTheDocument();
  await userEvent.click(screen.getByText('+ Создать кампанию'));
  expect(handlers.onStartCreate).toHaveBeenCalledTimes(1);
});

test('the inline create row submits a typed name and cancels on Escape', async () => {
  const handlers = renderCard({ creating: true, createName: 'Крипто' });

  await userEvent.click(screen.getByText('Создать кампанию'));
  expect(handlers.onCreate).toHaveBeenCalledTimes(1);

  const input = screen.getByLabelText('Название кампании');
  await userEvent.type(input, 'x');
  expect(handlers.onCreateName).toHaveBeenCalledWith('Криптоx');

  await userEvent.type(input, '{Enter}');
  expect(handlers.onCreate).toHaveBeenCalledTimes(2);

  await userEvent.type(input, '{Escape}');
  expect(handlers.onCancelCreate).toHaveBeenCalledTimes(1);

  await userEvent.click(screen.getByLabelText('Отменить создание'));
  expect(handlers.onCancelCreate).toHaveBeenCalledTimes(2);
});

test('a blank name cannot be submitted', async () => {
  const handlers = renderCard({ creating: true, createName: '   ' });

  expect(screen.getByText('Создать кампанию')).toBeDisabled();
  await userEvent.type(screen.getByLabelText('Название кампании'), '{Enter}');
  expect(handlers.onCreate).not.toHaveBeenCalled();
});
