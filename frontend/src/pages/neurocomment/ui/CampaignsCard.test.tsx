import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { CampaignsCard } from './CampaignsCard';

type Props = Parameters<typeof CampaignsCard>[0];

function renderCard(overrides: Partial<Props> = {}) {
  const props: Props = {
    campaignList: [],
    campaignId: 'c1',
    activeCampaign: null,
    boardChannels: [{ channel: '@a' }, { channel: '@b' }],
    openCampaignActions: null,
    onToggleActions: vi.fn(),
    onSelect: vi.fn(),
    onToggleStatus: vi.fn(),
    onEditPrompt: vi.fn(),
    onDelete: vi.fn(),
    onCreate: vi.fn(),
    channelFeedback: {},
    addingChannel: false,
    onStartAdd: vi.fn(),
    onCancelAdd: vi.fn(),
    channelInput: '',
    onChannelInput: vi.fn(),
    onAddChannel: vi.fn(),
    onRemoveChannel: vi.fn(),
    onCheckChannels: vi.fn(),
    checkingChannels: false,
    channelCheckStatus: {},
    ...overrides,
  };
  render(<CampaignsCard {...props} />);
  return props;
}

function chipFor(channel: string): HTMLElement {
  const removeButtons = screen.getAllByLabelText('Убрать канал');
  const chip = removeButtons
    .map((btn) => btn.closest('span'))
    .find((span) => span?.textContent?.includes(channel));
  if (!chip) throw new Error(`no chip for ${channel}`);
  return chip;
}

test('clicking "Проверить каналы" fires onCheckChannels', async () => {
  const props = renderCard();
  await userEvent.click(screen.getByText('Проверить каналы'));
  expect(props.onCheckChannels).toHaveBeenCalledOnce();
});

test('the check button is disabled and relabelled while checking', () => {
  renderCard({ checkingChannels: true });
  const button = screen.getByRole('button', { name: 'Проверка…' });
  expect(button).toBeDisabled();
});

test('the check button is disabled when no campaign is selected', () => {
  renderCard({ campaignId: null });
  expect(screen.getByRole('button', { name: 'Проверить каналы' })).toBeDisabled();
});

test('banned channels render red, ok channels render green, others gray', () => {
  renderCard({ channelCheckStatus: { '@a': 'banned', '@b': 'ok' } });
  expect(chipFor('@a').className).toContain('text-danger');
  expect(chipFor('@b').className).toContain('text-[#2e9e64]');
});

test('with no verdicts the chips stay the default gray', () => {
  renderCard();
  expect(chipFor('@a').className).toContain('bg-[#f4f3f0]');
});

const CAMPAIGN = {
  campaign_id: 'c1',
  name: 'tabacum',
  prompt: '',
  status: 'active' as const,
  created_at: '',
  updated_at: '',
};

function campaignCard(): HTMLElement {
  return screen.getByText('tabacum').closest('[role="button"]')!;
}

// Naming the two colours rather than counting `bg-*` utilities: jsdom has no
// cascade, so the tint that actually wins is unobservable, but the defect was
// `bg-white` sitting in the base list beside it — and a count also reddens on
// `bg-clip-padding` and friends, which carry no colour at all.
test('the selected campaign card carries the tint and not the white it lost to', () => {
  renderCard({ campaignList: [CAMPAIGN], campaignId: 'c1' });
  expect(campaignCard().className).toContain('bg-primary/[0.06]');
  expect(campaignCard().className).not.toContain('bg-white');
  // That tint is 6% opaque and the row's actions sit UNDER it, hidden only by being
  // covered — so the sliding surface has to bring its own opaque backdrop or
  // pause/edit/delete show through an unhovered card.
  expect(document.getElementById('camp-surf-c1')?.className).toContain('bg-white');
});

test('an unselected campaign card still carries a background of its own', () => {
  // The other branch: with the colour only asserted on the selected card,
  // deleting `bg-white` from this one goes unnoticed.
  renderCard({ campaignList: [CAMPAIGN], campaignId: null });
  expect(campaignCard().className).toContain('bg-white');
  expect(campaignCard().className).not.toContain('bg-primary/[0.06]');
});
