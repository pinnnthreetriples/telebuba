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
  expect(chipFor('@b').className).toContain('text-success');
});

test('with no verdicts the chips stay the default gray', () => {
  renderCard();
  expect(chipFor('@a').className).toContain('bg-canvas');
});

const CAMPAIGN = {
  campaign_id: 'c1',
  name: 'tabacum',
  prompt: '',
  status: 'active' as const,
  created_at: '',
  updated_at: '',
};

// Кнопка выбора накрывает карточку целиком (`absolute inset-0`), поэтому карточка — её
// родитель. Раньше здесь искался `[role="button"]`: сама карточка носила эту роль, не
// обрабатывая ни Enter, ни Space, и содержала внутри другую кнопку.
function selectButton(): HTMLElement {
  return screen.getByRole('button', { name: 'tabacum' });
}

function campaignCard(): HTMLElement {
  return selectButton().parentElement!;
}

// Naming the two colours rather than counting `bg-*` utilities: jsdom has no
// cascade, so the tint that actually wins is unobservable, but the defect was
// `bg-surface-card` sitting in the base list beside it — and a count also reddens on
// `bg-clip-padding` and friends, which carry no colour at all.
test('the selected campaign card carries the tint and not the white it lost to', () => {
  renderCard({ campaignList: [CAMPAIGN], campaignId: 'c1' });
  expect(campaignCard().className).toContain('bg-info-tint');
  expect(campaignCard().className).not.toContain('bg-surface-card');
  // The row's actions sit UNDER this card, hidden only by being covered, so the
  // sliding surface has to bring its own opaque backdrop or pause/edit/delete show
  // through an unhovered card. The tint used to be 6% alpha, which is how that was
  // found; it is opaque now and the backstop is still what the assertion guards.
  expect(document.getElementById('camp-surf-c1')?.className).toContain('bg-surface-card');
});

test('an unselected campaign card still carries a background of its own', () => {
  // The other branch: with the colour only asserted on the selected card,
  // deleting `bg-surface-card` from this one goes unnoticed.
  renderCard({ campaignList: [CAMPAIGN], campaignId: null });
  expect(campaignCard().className).toContain('bg-surface-card');
  expect(campaignCard().className).not.toContain('bg-info-tint');
});

// ── Клавиатура ─────────────────────────────────────────────────────────────────────
//
// Карточка была `div role="button" tabIndex={0}` с одним `onClick`. Такая пара
// фокусируется и НЕ активируется: ни Enter, ни Space у `div` не превращаются в клик, их
// пришлось бы обрабатывать руками, а роль тем временем обещала скринридеру кнопку.
// Внутри лежала вторая кнопка — шестерёнка, — чего ARIA не допускает.
//
// Настоящему `<button>` всё это достаётся от платформы, и тест утверждает именно это: не
// «есть обработчик», а «нажатие с клавиатуры выбирает».
test('карточку кампании можно выбрать с клавиатуры, и шестерёнка — отдельная цель', async () => {
  const onSelect = vi.fn();
  const onToggleActions = vi.fn();
  renderCard({ campaignList: [CAMPAIGN], campaignId: null, onSelect, onToggleActions });

  const select = screen.getByRole('button', { name: 'tabacum' });
  const gear = screen.getByRole('button', { name: 'Действия' });
  const pause = screen.getByRole('button', { name: 'Поставить на паузу' });

  // Шестерёнка не внутри кнопки выбора: вложенная кнопка — это то, что было.
  expect(select.contains(gear)).toBe(false);

  // Порядок обхода утверждается целиком, а не «карточка после N табов»: действия стоят
  // перед поверхностью в DOM, потому что поверхность их закрашивает сверху, и первый Tab
  // в строку попадает на них. Это и есть тот случай, из-за которого раскрытие по фокусу
  // обязательно: без него Tab уводил фокус под непрозрачную карточку.
  const surface = () => document.getElementById('camp-surf-c1');
  const REVEALED = /(^|\s)-translate-x-\[var\(--shift\)\]/;

  await userEvent.tab();
  await userEvent.tab();
  await userEvent.tab();
  expect(pause).toHaveFocus();
  expect(surface()?.className).toMatch(REVEALED);

  await userEvent.tab();
  await userEvent.tab();
  await userEvent.tab();
  expect(select).toHaveFocus();
  // Фокус ушёл с действий — поверхность вернулась на место.
  expect(surface()?.className).not.toMatch(REVEALED);
  // Видимый фокус, а не браузерное умолчание, снятое `outline-none`: обводка рецепта.
  expect(select.className).toContain('focus-visible:outline-focus');

  await userEvent.keyboard('{Enter}');
  expect(onSelect).toHaveBeenCalledWith('c1');

  await userEvent.keyboard(' ');
  expect(onSelect).toHaveBeenCalledTimes(2);

  // Следующая остановка — шестерёнка, и она открывает действия, а не выбирает.
  await userEvent.tab();
  expect(gear).toHaveFocus();
  await userEvent.keyboard('{Enter}');
  expect(onToggleActions).toHaveBeenCalledWith('c1');
  expect(onSelect).toHaveBeenCalledTimes(2);
});
