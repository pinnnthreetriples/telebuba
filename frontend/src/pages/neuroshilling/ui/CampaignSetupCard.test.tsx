import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { NeuroshillingCampaign } from '@/shared/api';

import { CampaignSetupCard } from './CampaignSetupCard';
import type { SetupDraft } from './setupDraft';
import { setupDraftOf } from './setupDraft';

const CAMPAIGN: NeuroshillingCampaign = {
  campaign_id: 'c1',
  name: 'Промо',
  mode: 'campaign',
  targets_raw: '@chat\n@other',
  autoresponder: 'neurodialog',
  reply_to_humans: true,
  reply_activity: 'active',
  listen_minutes: 45,
  created_at: 'now',
  updated_at: 'now',
};

// The page owns the draft, so the harness does too: a mock alone would leave the
// inputs controlled by a value that never moves, and a second keystroke would be
// typed against the first one's stale render.
function Harness({
  initial,
  onDraft,
  ...rest
}: {
  initial: SetupDraft;
  onDraft: (draft: SetupDraft) => void;
} & Omit<Parameters<typeof CampaignSetupCard>[0], 'draft' | 'onDraft'>) {
  const [draft, setDraft] = useState(initial);
  return (
    <CampaignSetupCard
      {...rest}
      draft={draft}
      onDraft={(next) => {
        setDraft(next);
        onDraft(next);
      }}
    />
  );
}

function renderCard(over: Partial<Parameters<typeof CampaignSetupCard>[0]> = {}) {
  const onDraft = vi.fn();
  const onSave = vi.fn();
  const { draft, ...props } = over;
  render(
    <Harness
      campaign={CAMPAIGN}
      initial={draft ?? setupDraftOf(CAMPAIGN)}
      onDraft={onDraft}
      dirty={false}
      reserveCount={0}
      live={false}
      onSave={onSave}
      busy={false}
      {...props}
    />,
  );
  return { onDraft, onSave };
}

test('the pause field says seconds, and min and max, not the mockup s minutes', () => {
  renderCard();
  expect(screen.getByText('Пауза между целями, сек')).toBeInTheDocument();
  expect(screen.getByLabelText('Минимальная пауза между целями, сек')).toHaveValue(10);
  expect(screen.getByLabelText('Максимальная пауза между целями, сек')).toHaveValue(20);
});

test('raising the minimum past the maximum drags the maximum with it', async () => {
  // `pause_min > pause_max` is a model-validator error, which reaches the operator
  // as an unreadable 422 blob.
  const { onDraft } = renderCard();
  const min = screen.getByLabelText('Минимальная пауза между целями, сек');
  await userEvent.clear(min);
  await userEvent.type(min, '90');

  const last = onDraft.mock.calls.at(-1)?.[0] as SetupDraft;
  expect(last.pauseMinSeconds).toBe(90);
  expect(last.pauseMaxSeconds).toBe(90);
});

test('the parallel option is disabled and says why, instead of collecting a 409', () => {
  renderCard();
  const options = screen.getAllByRole('radio', { name: /Последовательно|Параллельно/ });
  expect(options).toHaveLength(2);
  expect(screen.getByRole('radio', { name: /Параллельно/ })).toBeDisabled();
  expect(screen.getByText(/Пока недоступно/)).toBeInTheDocument();
});

test('the targets badge counts what is typed, and the field is monospaced', () => {
  renderCard();
  expect(screen.getByText('Целей: 2')).toBeInTheDocument();
  expect(screen.getByLabelText('Целевые чаты')).toHaveValue('@chat\n@other');
});

test('advanced settings hide behind a collapse whose badge counts real changes', async () => {
  renderCard({ draft: { ...setupDraftOf(CAMPAIGN), messagesPerHour: 4, reserveEnabled: true } });
  // Closed: nothing inside is reachable.
  expect(screen.queryByLabelText('Сообщений в час')).toBeNull();
  expect(screen.getByText('2')).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: /Расширенные настройки/ }));
  expect(screen.getByLabelText('Сообщений в час')).toHaveValue(4);
});

test('an emptied total travels as null, never as a zero the wire refuses', async () => {
  const { onDraft } = renderCard({ draft: { ...setupDraftOf(CAMPAIGN), totalPerAccount: 5 } });
  await userEvent.click(screen.getByRole('button', { name: /Расширенные настройки/ }));

  const total = screen.getByLabelText('Всего на аккаунт');
  expect(total).toHaveValue(5);
  await userEvent.clear(total);

  // `total_per_account` is `ge=1` on the wire: a 0 here would be a 422, and it
  // would also read as "this campaign may send nothing".
  expect((onDraft.mock.calls.at(-1)?.[0] as SetupDraft).totalPerAccount).toBeNull();
  expect(total).toHaveValue(null);
});

test('the two quota boxes clamp to the wire bounds instead of posting a 422', async () => {
  const { onDraft } = renderCard();
  await userEvent.click(screen.getByRole('button', { name: /Расширенные настройки/ }));

  const perHour = screen.getByLabelText('Сообщений в час');
  await userEvent.clear(perHour);
  await userEvent.type(perHour, '999');
  expect((onDraft.mock.calls.at(-1)?.[0] as SetupDraft).messagesPerHour).toBe(60);

  const perChat = screen.getByLabelText('Сообщений в чат в сутки');
  await userEvent.clear(perChat);
  await userEvent.type(perChat, '5');
  expect((onDraft.mock.calls.at(-1)?.[0] as SetupDraft).messagesPerChatPerDay).toBe(5);
});

test('the reserve badge counts the pool as it stands, not as the roster was arranged', async () => {
  // A promoted account has its reserve flag cleared server-side, so the number the
  // page passes in is what is left — and zero is the state worth seeing.
  renderCard({ reserveCount: 2 });
  await userEvent.click(screen.getByRole('button', { name: /Расширенные настройки/ }));

  expect(screen.getByText('В резерве: 2')).toBeInTheDocument();
});

test('the reserve switch and the sequential option write to the draft', async () => {
  const { onDraft } = renderCard();
  await userEvent.click(screen.getByRole('radio', { name: /Последовательно/ }));
  expect((onDraft.mock.calls.at(-1)?.[0] as SetupDraft).runMode).toBe('sequential');

  await userEvent.click(screen.getByRole('button', { name: /Расширенные настройки/ }));
  await userEvent.click(screen.getByRole('switch', { name: 'Резервные аккаунты' }));
  expect((onDraft.mock.calls.at(-1)?.[0] as SetupDraft).reserveEnabled).toBe(true);
});

test('the stage-six controls are shown with their stored values and are inert', async () => {
  const { onDraft } = renderCard();
  await userEvent.click(screen.getByRole('button', { name: /Расширенные настройки/ }));

  expect(screen.getByText('скоро')).toBeInTheDocument();
  const humans = screen.getByRole('switch', { name: 'Отвечать реальным людям' });
  expect(humans).toBeDisabled();
  expect(humans).toHaveAttribute('aria-checked', 'true');
  // The stored value is on screen, so the operator sees what the column holds.
  expect(screen.getByRole('radio', { name: 'Нейродиалог' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
  expect(screen.getByRole('radio', { name: 'Активно' })).toBeDisabled();
  expect(screen.getByLabelText('Слушать чат, мин')).toBeDisabled();

  await userEvent.click(humans);
  expect(onDraft).not.toHaveBeenCalled();
});

test('the segmented choices announce themselves as radio groups', async () => {
  renderCard();
  await userEvent.click(screen.getByRole('button', { name: /Расширенные настройки/ }));
  expect(screen.getByRole('radiogroup', { name: 'Режим запуска' })).toBeInTheDocument();
  expect(screen.getByRole('radiogroup', { name: 'Автоответчик' })).toBeInTheDocument();
  expect(screen.getByRole('radiogroup', { name: 'Активность ответов' })).toBeInTheDocument();
});

test('save is explicit: it waits for a change and fires once', async () => {
  const { onSave } = renderCard({ dirty: true });
  await userEvent.click(screen.getByText('Сохранить настройки'));
  expect(onSave).toHaveBeenCalledTimes(1);
});

test('a clean form cannot be saved', () => {
  renderCard();
  expect(screen.getByText('Сохранить настройки')).toBeDisabled();
});

test('a running campaign locks the whole card and says so', () => {
  renderCard({ live: true, dirty: true });
  expect(screen.getByText(/Кампания запущена/)).toBeInTheDocument();
  expect(screen.getByLabelText('Целевые чаты')).toBeDisabled();
  expect(screen.getByRole('radio', { name: /Последовательно/ })).toBeDisabled();
  // The server answers 409 `campaign_running` to the whole PUT.
  expect(screen.getByText('Сохранить настройки')).toBeDisabled();
});
