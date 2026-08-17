import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type {
  NeuroshillingBoardAccount,
  NeuroshillingCampaign,
  NeuroshillingRole,
  NeuroshillingStep,
} from '@/shared/api';

import { LaunchCard } from './LaunchCard';

const CAMPAIGN: NeuroshillingCampaign = {
  campaign_id: 'c1',
  name: 'Промо',
  mode: 'campaign',
  scenario_status: 'approved',
  run_mode: 'sequential',
  status: 'idle',
  created_at: 'now',
  updated_at: 'now',
};

const ROLES: NeuroshillingRole[] = [
  { role_id: 'r1', name: 'Скептик', created_at: 'now' },
  { role_id: 'r2', name: 'Довольный', created_at: 'now' },
];

const STEPS: NeuroshillingStep[] = [
  {
    step_id: 's1',
    position: 1,
    kind: 'message',
    role_id: 'r1',
    delay_min_seconds: 60,
    delay_max_seconds: 180,
  },
  {
    step_id: 's2',
    position: 2,
    kind: 'message',
    role_id: 'r2',
    delay_min_seconds: 30,
    delay_max_seconds: 90,
  },
  { step_id: 's3', position: 3, kind: 'reaction', role_id: 'r1', emoji: '👍' },
];

const POOL: NeuroshillingBoardAccount[] = [
  { account_id: 'a1', title: 'Алиса', assigned: true, role_id: 'r1' },
  { account_id: 'a2', title: 'Борис', assigned: true, role_id: 'r2' },
  { account_id: 'a3', title: 'Виктор' },
];

function renderCard(over: Partial<Parameters<typeof LaunchCard>[0]> = {}) {
  const onStart = vi.fn();
  const onStop = vi.fn();
  const onClearLogs = vi.fn();
  render(
    <LaunchCard
      campaign={CAMPAIGN}
      run={{ status: 'idle', sent: 0, total: 4 }}
      pool={POOL}
      targets={['@chat', '@other']}
      roles={ROLES}
      steps={STEPS}
      logLines={[]}
      onStart={onStart}
      onStop={onStop}
      onClearLogs={onClearLogs}
      busy={false}
      {...over}
    />,
  );
  return { onStart, onStop, onClearLogs };
}

test('a launchable campaign offers Start with no reasons listed', async () => {
  const { onStart } = renderCard();
  const start = screen.getByRole('button', { name: 'Запустить' });
  expect(start).toBeEnabled();
  expect(screen.queryByText(/Сценарий не утверждён/)).toBeNull();

  await userEvent.click(start);
  expect(onStart).toHaveBeenCalledTimes(1);
});

test('the substitution counter is shown at zero as well as after a replacement', () => {
  // Zero is an answer, not an absence: "has Telegram eaten any of my accounts" is
  // the question this number is on the card to settle.
  renderCard();
  expect(screen.getByText('Замен: 0')).toBeInTheDocument();

  renderCard({ run: { status: 'running', sent: 1, total: 4, substitutions: 2 } });
  expect(screen.getByText('Замен: 2')).toBeInTheDocument();
});

test('a draft scenario disables Start and names the reason on THIS card', () => {
  // The approval dies three cards up, on any role or step edit; the consequence
  // only surfaces here.
  renderCard({ campaign: { ...CAMPAIGN, scenario_status: 'draft' } });
  expect(screen.getByRole('button', { name: 'Запустить' })).toBeDisabled();
  expect(screen.getByText(/Сценарий не утверждён/)).toBeInTheDocument();
  expect(screen.getByText('Сценарий: черновик')).toBeInTheDocument();
});

test('an approved scenario is stated on the card too, not only three cards up', () => {
  renderCard();
  expect(screen.getByText('✓ Сценарий утверждён')).toBeInTheDocument();
});

test('no saved targets is a stated reason, not a silent refusal', () => {
  renderCard({ targets: [] });
  expect(screen.getByRole('button', { name: 'Запустить' })).toBeDisabled();
  expect(screen.getByText(/Нет сохранённых целей/)).toBeInTheDocument();
});

test('too few playing accounts names the count, and reserve seats do not count', () => {
  renderCard({
    pool: [
      { account_id: 'a1', title: 'Алиса', assigned: true, role_id: 'r1' },
      { account_id: 'a2', title: 'Борис', assigned: true, role_id: 'r2', is_reserve: true },
    ],
  });
  // The server counts `active and not reserve`, so the card must too.
  expect(screen.getByText(/Мало аккаунтов: сейчас 1/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Запустить' })).toBeDisabled();
});

test('a role nobody plays is named, so the operator knows which one to staff', () => {
  renderCard({
    pool: [
      { account_id: 'a1', title: 'Алиса', assigned: true, role_id: 'r1' },
      { account_id: 'a2', title: 'Борис', assigned: true, role_id: 'r1' },
    ],
  });
  expect(screen.getByText(/Роль «Довольный» без аккаунта/)).toBeInTheDocument();
});

test('a step with no role at all is reported by its position', () => {
  renderCard({ steps: [{ ...STEPS[0]!, role_id: null }, STEPS[1]!] });
  expect(screen.getByText(/У шага #1 не выбрана роль/)).toBeInTheDocument();
});

test('an account held elsewhere is named together with who holds it', () => {
  renderCard({
    pool: [
      { account_id: 'a1', title: 'Алиса', assigned: true, role_id: 'r1', busy_owner: 'warming' },
      { account_id: 'a2', title: 'Борис', assigned: true, role_id: 'r2' },
    ],
  });
  expect(screen.getByText(/Аккаунт «Алиса» занят: занят прогревом/)).toBeInTheDocument();
});

test('parallel run mode is refused with its reason rather than at the server', () => {
  renderCard({ campaign: { ...CAMPAIGN, run_mode: 'parallel' } });
  expect(screen.getByText(/Параллельный режим пока недоступен/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Запустить' })).toBeDisabled();
});

test('every blocking reason is listed, not just the first', () => {
  renderCard({ campaign: { ...CAMPAIGN, scenario_status: 'draft' }, targets: [] });
  expect(screen.getByText(/Сценарий не утверждён/)).toBeInTheDocument();
  expect(screen.getByText(/Нет сохранённых целей/)).toBeInTheDocument();
});

test('a running campaign swaps Start for Stop and shows the live pill', async () => {
  const { onStop } = renderCard({
    campaign: { ...CAMPAIGN, status: 'running' },
    run: { status: 'running', sent: 1, total: 4 },
  });
  expect(screen.queryByRole('button', { name: 'Запустить' })).toBeNull();
  expect(screen.getByText('LIVE')).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Остановить' }));
  expect(onStop).toHaveBeenCalledTimes(1);
});

test('a stopping campaign cannot be stopped twice', () => {
  renderCard({ run: { status: 'stopping', sent: 2, total: 4 } });
  expect(screen.getByRole('button', { name: 'Остановить' })).toBeDisabled();
});

test('progress counts delivered MESSAGE steps against targets x message steps', () => {
  // Two targets x two message steps = 4. The reaction step is journalled but
  // counted in neither, so a skipped reaction never reads as lost progress.
  renderCard({ run: { status: 'running', sent: 3, total: 4 } });
  expect(screen.getByText('3 из 4 реплик')).toBeInTheDocument();
  const bar = screen.getByRole('progressbar', { name: 'Прогресс прогона: отправленные реплики' });
  expect(bar).toHaveAttribute('aria-valuenow', '3');
  expect(bar).toHaveAttribute('aria-valuemax', '4');
});

test('the listening counters appear only while a run is really reading', () => {
  // The three switches are on the campaign row already; what the operator cannot
  // see from there is whether anything is acting on them right now.
  renderCard({ run: { status: 'running', sent: 0, total: 4 } });
  expect(screen.queryByText('Прослушка')).toBeNull();

  renderCard({
    run: {
      status: 'running',
      sent: 0,
      total: 4,
      listening: true,
      chat_messages_seen: 12,
      human_replies_sent: 3,
    },
  });
  expect(screen.getByText('Прослушка')).toBeInTheDocument();
  expect(screen.getByText('прочитано: 12')).toBeInTheDocument();
  expect(screen.getByText('ответов людям: 3')).toBeInTheDocument();
});

test('the tiles count the roster, the targets, the roles and the message steps', () => {
  renderCard();
  expect(screen.getByText('Аккаунтов').previousSibling).toHaveTextContent('2');
  expect(screen.getByText('Реплик').previousSibling).toHaveTextContent('2');
  // 5:00 — the midpoint of EVERY step's delay range, summed. The reaction is not
  // counted as a message but the engine still sleeps before it, so it belongs in
  // the duration even though it is absent from the "Реплик" tile.
  expect(screen.getByText('Диалог').previousSibling).toHaveTextContent('5:00');
});

test('a failed run shows the exception class, which is all the server sends', () => {
  renderCard({ run: { status: 'failed', sent: 1, total: 4, last_error_type: 'FloodWaitError' } });
  expect(screen.getByText(/FloodWaitError/)).toBeInTheDocument();
});

test('halted accounts are named rather than left as ids', () => {
  renderCard({ run: { status: 'running', sent: 1, total: 4, halted_accounts: ['a1'] } });
  expect(screen.getByText(/Telegram вывел из прогона: Алиса/)).toBeInTheDocument();
});

test('the log panel is the shared terminal, titled for this page, and asks before clearing', async () => {
  const { onClearLogs } = renderCard({
    logLines: [
      {
        id: 1,
        created_at: '2026-07-11T10:00:00+00:00',
        level: 'INFO',
        status: 'success',
        account_id: 'a1',
        event: 'neuroshilling_message_sent',
        extra: {},
      },
    ],
  });
  expect(screen.getByText('Лог кампании')).toBeInTheDocument();
  // The account column is named from the pool, not left as an opaque id.
  expect(screen.getByRole('button', { name: 'Алиса' })).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Очистить лог' }));
  // The card only ASKS: the count and the confirmation live on the page.
  expect(onClearLogs).toHaveBeenCalledTimes(1);
});
