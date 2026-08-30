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

import { PipelineCard } from './PipelineCard';

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

function renderCard(over: Partial<Parameters<typeof PipelineCard>[0]> = {}) {
  const onStart = vi.fn();
  const onStop = vi.fn();
  render(
    <PipelineCard
      campaign={CAMPAIGN}
      run={{ status: 'idle', sent: 0, total: 4 }}
      pool={POOL}
      targets={['@chat', '@other']}
      roles={ROLES}
      steps={STEPS}
      onStart={onStart}
      onStop={onStop}
      busy={false}
      {...over}
    />,
  );
  return { onStart, onStop };
}

test('a launchable campaign offers Start and says so instead of listing reasons', async () => {
  const { onStart } = renderCard();
  const start = screen.getByRole('button', { name: 'Запустить' });
  expect(start).toBeEnabled();
  expect(screen.getByText('Готово к запуску')).toBeInTheDocument();
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

test('the scenario node carries the approval, so the pipeline states it in place', () => {
  renderCard();
  expect(screen.getByText('утверждён')).toBeInTheDocument();

  renderCard({ campaign: { ...CAMPAIGN, scenario_status: 'draft' } });
  expect(screen.getByText('черновик')).toBeInTheDocument();
});

test('a draft scenario disables Start and names the reason', () => {
  // The approval dies in the scenario editor, on any role or step edit; the
  // consequence surfaces here, where the button is.
  renderCard({ campaign: { ...CAMPAIGN, scenario_status: 'draft' } });
  expect(screen.getByRole('button', { name: 'Запустить' })).toBeDisabled();
  expect(screen.getByText(/Сценарий не утверждён/)).toBeInTheDocument();
});

test('the summary counts the reasons rather than only naming one', () => {
  // The full list lives in the sidebar's checks banner. What this line must not do
  // is imply there is a single thing left to fix when there are two.
  renderCard({ campaign: { ...CAMPAIGN, scenario_status: 'draft' }, targets: [] });
  expect(screen.getByText(/Осталось 2 замечания/)).toBeInTheDocument();
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

test('parallel run mode is refused with its reason rather than at the server', () => {
  renderCard({ campaign: { ...CAMPAIGN, run_mode: 'parallel' } });
  expect(screen.getByText(/Параллельный режим пока недоступен/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Запустить' })).toBeDisabled();
});

test('the accounts node counts the roles somebody actually plays', () => {
  renderCard();
  expect(screen.getByText('2 из 2 ролей')).toBeInTheDocument();

  renderCard({
    pool: [
      { account_id: 'a1', title: 'Алиса', assigned: true, role_id: 'r1' },
      { account_id: 'a2', title: 'Борис', assigned: true, role_id: 'r1' },
    ],
  });
  expect(screen.getByText('1 из 2 ролей')).toBeInTheDocument();
});

test('a running campaign swaps Start for Stop', async () => {
  const { onStop } = renderCard({
    campaign: { ...CAMPAIGN, status: 'running' },
    run: { status: 'running', sent: 1, total: 4 },
  });
  expect(screen.queryByRole('button', { name: 'Запустить' })).toBeNull();
  expect(screen.getByText('Работает')).toBeInTheDocument();

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

test('a revive run shows a counter instead of a bar', () => {
  // It loops until it is stopped, so the server sends no total and there is
  // nothing for a percentage to be a percentage OF.
  renderCard({
    campaign: { ...CAMPAIGN, mode: 'revive' },
    run: { status: 'running', sent: 7, total: 0 },
  });

  expect(screen.getByText('Отправлено: 7')).toBeInTheDocument();
  expect(
    screen.queryByRole('progressbar', { name: 'Прогресс прогона: отправленные реплики' }),
  ).toBeNull();
});

test('the listening counters appear only while a run is really reading', () => {
  // The three switches are on the campaign row already; what the operator cannot
  // see from there is whether anything is acting on them right now. Asserted on the
  // counters rather than on the word: «Прослушка» is also a pipeline node, and that
  // node is on the card whether anything is listening or not.
  renderCard({ run: { status: 'running', sent: 0, total: 4 } });
  expect(screen.queryByText('прочитано: 12')).toBeNull();

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
  expect(screen.getByText('Диалог в цели').previousSibling).toHaveTextContent('5:00');
});

test('a failed run shows the exception class, which is all the server sends', () => {
  renderCard({ run: { status: 'failed', sent: 1, total: 4, last_error_type: 'FloodWaitError' } });
  expect(screen.getByText(/FloodWaitError/)).toBeInTheDocument();
});

test('halted accounts are named rather than left as ids', () => {
  renderCard({ run: { status: 'running', sent: 1, total: 4, halted_accounts: ['a1'] } });
  expect(screen.getByText(/Telegram вывел из прогона: Алиса/)).toBeInTheDocument();
});
