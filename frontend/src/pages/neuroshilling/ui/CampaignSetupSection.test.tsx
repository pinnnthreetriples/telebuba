import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { NeuroshillingCampaign, NeuroshillingScenario } from '@/shared/api';

import { CampaignSetupSection } from './CampaignSetupSection';
import { draftOf } from './scenarioDraft';
import type { SetupDraft } from './setupDraft';
import { setupDraftOf } from './setupDraft';

const SCENARIO: NeuroshillingScenario = {
  campaign_id: 'c1',
  scenario_status: 'draft',
  roles: [],
  steps: [],
};

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

// The page owns both drafts, so the harness does too: a mock alone would leave the
// inputs controlled by a value that never moves, and a second keystroke would be
// typed against the first one's stale render.
function Harness({
  initial,
  onDraft,
  ...rest
}: {
  initial: SetupDraft;
  onDraft: (draft: SetupDraft) => void;
} & Omit<
  Parameters<typeof CampaignSetupSection>[0],
  'draft' | 'onDraft' | 'scenario' | 'onScenario'
>) {
  const [draft, setDraft] = useState(initial);
  // Секция читает и черновик сценария — режим кампании, «разные голоса» и «читать чат»
  // живут там, потому что уезжают вторым PUT.
  const [scenario, setScenario] = useState(() => draftOf(CAMPAIGN, SCENARIO));
  return (
    <CampaignSetupSection
      {...rest}
      draft={draft}
      onDraft={(next) => {
        setDraft(next);
        onDraft(next);
      }}
      scenario={scenario}
      onScenario={setScenario}
    />
  );
}

function renderCard(over: Partial<Parameters<typeof CampaignSetupSection>[0]> = {}) {
  const onDraft = vi.fn();
  const { draft, ...props } = over;
  render(
    <Harness
      initial={draft ?? setupDraftOf(CAMPAIGN)}
      onDraft={onDraft}
      reserveCount={0}
      live={false}
      {...props}
    />,
  );
  return { onDraft };
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

test('the parallel option is disabled on its own, with the reason on it', () => {
  // Групповой `disabled` этого сказать не может: «Последовательно» остаётся живым.
  renderCard();
  const parallel = screen.getByRole('radio', { name: 'Параллельно' });
  expect(parallel).toBeDisabled();
  expect(parallel.getAttribute('title')).toMatch(/Пока недоступно/);
  expect(screen.getByRole('radio', { name: 'Последовательно' })).toBeEnabled();
});

test('targets are chips that can be removed one at a time', async () => {
  const { onDraft } = renderCard();
  expect(screen.getByText('2 цели')).toBeInTheDocument();
  expect(screen.getByText('@chat')).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Убрать @chat' }));
  expect((onDraft.mock.calls.at(-1)?.[0] as SetupDraft).targetsRaw).toBe('@other');
});

test('a pasted block becomes one chip per chat, so bulk entry survives the textarea', async () => {
  // Поля-простыни больше нет, но вставка списком осталась: строка ввода режется тем же
  // разделителем, что и сохранённое значение.
  const { onDraft } = renderCard();
  await userEvent.click(screen.getByRole('button', { name: '+ Чат' }));
  await userEvent.type(screen.getByLabelText('+ Чат'), '@one, @two @three{Enter}');

  const raw = (onDraft.mock.calls.at(-1)?.[0] as SetupDraft).targetsRaw;
  expect(raw.split(String.fromCharCode(10))).toEqual(['@chat', '@other', '@one', '@two', '@three']);
});

test('the limits row summarises what is inside instead of opening a panel in place', async () => {
  renderCard({ draft: { ...setupDraftOf(CAMPAIGN), messagesPerHour: 4, reserveEnabled: true } });
  expect(screen.queryByLabelText('Сообщений в час')).toBeNull();
  expect(screen.getByText(/4 в час · 3 в чат за сутки · резерв вкл/)).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Настроить' }));
  expect(screen.getByLabelText('Сообщений в час')).toHaveValue(4);
});

test('an emptied total travels as null, never as a zero the wire refuses', async () => {
  const { onDraft } = renderCard({ draft: { ...setupDraftOf(CAMPAIGN), totalPerAccount: 5 } });
  await userEvent.click(screen.getByRole('button', { name: 'Настроить' }));

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
  await userEvent.click(screen.getByRole('button', { name: 'Настроить' }));

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
  await userEvent.click(screen.getByRole('button', { name: 'Настроить' }));

  expect(screen.getByText('В резерве: 2')).toBeInTheDocument();
});

test('the reserve switch and the sequential option write to the draft', async () => {
  const { onDraft } = renderCard();
  await userEvent.click(screen.getByRole('radio', { name: /Последовательно/ }));
  expect((onDraft.mock.calls.at(-1)?.[0] as SetupDraft).runMode).toBe('sequential');

  await userEvent.click(screen.getByRole('button', { name: 'Настроить' }));
  await userEvent.click(screen.getByRole('switch', { name: 'Резервные аккаунты' }));
  expect((onDraft.mock.calls.at(-1)?.[0] as SetupDraft).reserveEnabled).toBe(true);
});

test('the listening controls write to the draft and show what is stored', async () => {
  const { onDraft } = renderCard();
  await userEvent.click(screen.getByRole('button', { name: 'Настроить' }));

  expect(screen.getByText('Прослушка чата')).toBeInTheDocument();
  const humans = screen.getByRole('switch', { name: 'Отвечать реальным людям' });
  expect(humans).toHaveAttribute('aria-checked', 'true');
  expect(screen.getByRole('radio', { name: 'Нейродиалог' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
  expect(screen.getByLabelText('Слушать чат, мин')).toHaveValue(45);

  await userEvent.click(humans);
  expect((onDraft.mock.calls.at(-1)?.[0] as SetupDraft).replyToHumans).toBe(false);

  await userEvent.click(screen.getByRole('radio', { name: 'Спокойно' }));
  expect((onDraft.mock.calls.at(-1)?.[0] as SetupDraft).replyActivity).toBe('calm');

  await userEvent.click(screen.getByRole('radio', { name: 'Выключен' }));
  expect((onDraft.mock.calls.at(-1)?.[0] as SetupDraft).autoresponder).toBe('off');
});

test('the listening window clamps to the wire bound', async () => {
  const { onDraft } = renderCard();
  await userEvent.click(screen.getByRole('button', { name: 'Настроить' }));

  const window = screen.getByLabelText('Слушать чат, мин');
  await userEvent.clear(window);
  await userEvent.type(window, '9999');

  expect((onDraft.mock.calls.at(-1)?.[0] as SetupDraft).listenMinutes).toBe(1440);
});

test('the warning appears only when BOTH switches are on', async () => {
  // The server requires both before a stranger's message can provoke anything, so
  // showing the warning for either one alone would name a risk that is not there.
  const draft = { ...setupDraftOf(CAMPAIGN), autoresponder: 'off' as const };
  renderCard({ draft });
  await userEvent.click(screen.getByRole('button', { name: 'Настроить' }));
  expect(screen.queryByText(/Текст из чата пишут посторонние/)).toBeNull();

  await userEvent.click(screen.getByRole('radio', { name: 'Нейродиалог' }));
  expect(screen.getByText(/Текст из чата пишут посторонние/)).toBeInTheDocument();
});

test('the segmented choices announce themselves as radio groups', async () => {
  renderCard();
  await userEvent.click(screen.getByRole('button', { name: 'Настроить' }));
  expect(screen.getByRole('radiogroup', { name: 'Режим запуска' })).toBeInTheDocument();
  expect(screen.getByRole('radiogroup', { name: 'Автоответчик' })).toBeInTheDocument();
  expect(screen.getByRole('radiogroup', { name: 'Активность ответов' })).toBeInTheDocument();
});

test('a running campaign locks every control, not just the save it no longer owns', async () => {
  // Сервер отказывает всему PUT с `campaign_running`, поэтому замок висит на КАЖДОМ
  // поле: видно, что именно нельзя тронуть, а не одна серая кнопка в подвале.
  renderCard({ live: true });
  expect(screen.getByRole('button', { name: '+ Чат' })).toBeDisabled();
  expect(screen.getByRole('radio', { name: 'Последовательно' })).toBeDisabled();
  expect(screen.getByLabelText('Минимальная пауза между целями, сек')).toBeDisabled();
  expect(screen.getByRole('switch', { name: 'Разные голоса у ролей' })).toBeDisabled();
  // Прослушка едет тем же PUT, значит запирается вместе со всем остальным.
  expect(screen.getByRole('switch', { name: 'Отвечать реальным людям' })).toBeDisabled();
  expect(screen.getByLabelText('Слушать чат, мин')).toBeDisabled();
});
