import { useTranslation } from 'react-i18next';

import type {
  NeuroshillingBoardAccount,
  NeuroshillingCampaign,
  NeuroshillingRole,
  NeuroshillingRunStatus,
  NeuroshillingStep,
} from '@/shared/api';
import { Badge, Notice, PipelineCard, ProgressBar, type StepperStep } from '@/shared/ui';

import { CampaignStatusBadge } from './CampaignStatusBadge';
import { launchBlockers } from './launchChecks';
import { clock, dialogueSeconds } from './scenarioDraft';

// Конвейер кампании: где она стоит, что мешает её запустить и сама кнопка запуска. Карточка
// — общая `PipelineCard`; здесь только то, что принадлежит нейрошиллингу: какие узлы
// пройдены, сводка замечаний, числа и то, что лежит под ними (отправка, прослушка, исходы).
//
// Узел конвейера либо пройден, либо нет. Состояния «сейчас здесь» нет умышленно: конвейер
// показывает готовность к запуску, а не позицию бегунка, и «вы здесь» на неупорядоченном
// списке условий соврало бы о порядке, которого нет.
//
// Хуков, кроме `useTranslation`, нет: каждый запрос живёт на странице, как и у соседей.
export function LaunchPipeline({
  campaign,
  run,
  pool,
  targets,
  roles,
  steps,
  onStart,
  onStop,
  busy,
}: {
  campaign: NeuroshillingCampaign;
  run: NeuroshillingRunStatus;
  // Весь пул, чтобы остановленные аккаунты можно было назвать по имени; ростер — его
  // подмножество с `assigned`.
  pool: NeuroshillingBoardAccount[];
  targets: string[];
  roles: NeuroshillingRole[];
  steps: NeuroshillingStep[];
  onStart: () => void;
  onStop: () => void;
  busy: boolean;
}) {
  const { t } = useTranslation();
  const roster = pool.filter((account) => account.assigned);
  const status = run.status ?? 'idle';
  const live = status === 'running' || status === 'stopping';
  const approved = (campaign.scenario_status ?? 'draft') === 'approved';
  const blockers = launchBlockers(t, campaign, roster, targets, roles, steps);
  const messageSteps = steps.filter((step) => step.kind === 'message').length;
  const staffed = roles.filter((role) =>
    roster.some((account) => account.role_id === role.role_id),
  ).length;

  const sent = run.sent ?? 0;
  const total = run.total ?? 0;
  // Кампания режима `revive` крутится, пока её не остановят, поэтому сервер не шлёт
  // знаменателя и делить не на что. Счётчик ЗАМЕНЯЕТ полосу, а не стоит рядом с пустой.
  const looping = campaign.mode === 'revive';
  const titleOf = (accountId: string) =>
    pool.find((account) => account.account_id === accountId)?.title ?? accountId;
  const halted = run.halted_accounts ?? [];

  const node = (id: string, done: boolean, caption: string): StepperStep => ({
    id,
    label: t(`neuroshilling.pipeline.node.${id}`),
    caption,
    state: done ? 'done' : 'upcoming',
  });
  const nodes = [
    node(
      'scenario',
      approved,
      t(`neuroshilling.pipeline.sub.scenario.${approved ? 'approved' : 'draft'}`),
    ),
    node(
      'accounts',
      roles.length > 0 && staffed === roles.length,
      t('neuroshilling.pipeline.sub.accounts', { staffed, count: roles.length }),
    ),
    node('targets', targets.length > 0, t('neuroshilling.targetsCount', { count: targets.length })),
    node(
      'intro',
      live,
      t('neuroshilling.pipeline.sub.intro', {
        min: campaign.pause_min_seconds ?? 0,
        max: campaign.pause_max_seconds ?? 0,
      }),
    ),
    node('dialogue', live, t('neuroshilling.pipeline.sub.dialogue', { count: messageSteps })),
    node(
      'listen',
      run.listening === true,
      t('neuroshilling.pipeline.sub.listen', { count: campaign.listen_minutes ?? 0 }),
    ),
  ];

  return (
    <PipelineCard
      title={
        <>
          {t('neuroshilling.pipeline.title')}
          <span className="text-action-primary"> — {campaign.name}</span>
        </>
      }
      status={<CampaignStatusBadge status={status} />}
      running={live}
      toggleDisabled={live ? busy || status === 'stopping' : busy || blockers.length > 0}
      startLabel={t('neuroshilling.launch.start')}
      stopLabel={t('neuroshilling.launch.stop')}
      onToggle={live ? onStop : onStart}
      steps={nodes}
      narrow="list"
      // Одна строка вместо списка причин: перечисление уже есть — в сводке замечаний
      // сайдбара, которая для этого и заведена. Здесь — ПЕРВАЯ причина и их число.
      notice={
        blockers.length > 0
          ? {
              tone: 'info',
              text: t('neuroshilling.pipeline.remaining', {
                first: blockers[0],
                count: blockers.length,
              }),
            }
          : { tone: 'success', text: t('neuroshilling.pipeline.ready') }
      }
      stats={[
        { label: t('neuroshilling.launch.tile.accounts'), value: roster.length },
        { label: t('neuroshilling.launch.tile.targets'), value: targets.length },
        { label: t('neuroshilling.launch.tile.roles'), value: roles.length },
        { label: t('neuroshilling.launch.tile.messages'), value: messageSteps },
        { label: t('neuroshilling.launch.tile.dialogue'), value: clock(dialogueSeconds(steps)) },
      ]}
    >
      {/* `sent` / `total` считают только шаги-СООБЩЕНИЯ: реакции журналируются, но
          пропущенная реакция — не потерянный прогресс, и полоса, считающая каждый шаг,
          врала бы вниз. */}
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Badge className="tabular-nums">
          {t('neuroshilling.launch.substitutions', { n: run.substitutions ?? 0 })}
        </Badge>
        <span className="ml-auto type-small tabular-nums">
          {t(looping ? 'neuroshilling.launch.sentTotal' : 'neuroshilling.launch.progress', {
            sent,
            total,
          })}
        </span>
      </div>
      {looping ? null : (
        <ProgressBar
          label={t('neuroshilling.launch.progressLabel')}
          value={sent}
          max={total}
          className="w-full"
        />
      )}

      {/* Показывается только пока прогон действительно читает: три переключателя и так
          лежат в строке кампании, а чего по ним не видно — работает ли сейчас хоть один. */}
      {run.listening === true ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-md bg-canvas px-3 py-2 type-small tabular-nums">
          <span className="font-medium">{t('neuroshilling.launch.listening')}</span>
          <span>{t('neuroshilling.launch.chatSeen', { n: run.chat_messages_seen ?? 0 })}</span>
          <span>{t('neuroshilling.launch.humanReplies', { n: run.human_replies_sent ?? 0 })}</span>
        </div>
      ) : null}

      {/* Оба уведомления об исходе — в одной колонке с `gap`: расстояние между ними и
          до того, что выше, принадлежит ей, а не им. */}
      {status === 'failed' || halted.length > 0 ? (
        <div className="mt-3 flex flex-col gap-3">
          {status === 'failed' && run.last_error_type ? (
            <Notice tone="danger" bordered={false}>
              {t('neuroshilling.launch.failed', { type: run.last_error_type })}
            </Notice>
          ) : null}
          {halted.length > 0 ? (
            <Notice tone="warning" bordered={false}>
              {t('neuroshilling.launch.halted', { names: halted.map(titleOf).join(', ') })}
            </Notice>
          ) : null}
        </div>
      ) : null}
    </PipelineCard>
  );
}
