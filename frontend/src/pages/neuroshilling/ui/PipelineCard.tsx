import { useTranslation } from 'react-i18next';

import type {
  NeuroshillingBoardAccount,
  NeuroshillingCampaign,
  NeuroshillingRole,
  NeuroshillingRunStatus,
  NeuroshillingStep,
} from '@/shared/api';
import { Badge, Button, Card, Notice } from '@/shared/ui';

import { CampaignStatusBadge } from './CampaignStatusBadge';
import { launchBlockers } from './launchChecks';
import { clock, dialogueSeconds } from './scenarioDraft';

// Один узел конвейера: пройден он или ещё нет. `done` — единственное различие, и оно
// красит и точку, и подпись: пройденный узел набран чернилами страницы, непройденный —
// приглушёнными. Промежуточного состояния «сейчас здесь» нет умышленно: конвейер
// показывает готовность к запуску, а не позицию бегунка, и стрелка «вы здесь» на
// неупорядоченном списке условий соврала бы о порядке, которого нет.
//
// Рельса собрана из ДВУХ половин внутри самого узла, а не проведена одной линией за
// точками. Причина не в красоте: линия за точками должна начинаться под центром первой и
// кончаться под центром последней, то есть быть вдвинутой на пол-колонки, а половина
// колонки — дробь, и дробей у приложения нет: `width` и `spacing` стоят в КОРНЕ
// `theme`, поэтому шкалы Tailwind заменены целиком и `w-1/2` не выпускает правила. Две
// половины по `flex-1` дают ту же линию, ничего не зная о ширине колонки.
function Node({
  label,
  sub,
  done,
  first = false,
  last = false,
}: {
  label: string;
  sub: string;
  done: boolean;
  first?: boolean;
  last?: boolean;
}) {
  return (
    <div className="flex min-w-0 flex-col items-center gap-sm text-center">
      <div className="flex w-full items-center">
        <span className={`h-rail flex-1 bg-line ${first ? 'invisible' : ''}`} />
        <span
          className={`size-node shrink-0 rounded-full border-2 ${done ? 'border-action-primary bg-action-primary' : 'border-line-strong bg-surface-card'}`}
        />
        <span className={`h-rail flex-1 bg-line ${last ? 'invisible' : ''}`} />
      </div>
      <span className={done ? 'type-item-title' : 'type-caption'}>{label}</span>
      <span className="-mt-xs type-caption">{sub}</span>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="px-md py-md">
      <div className="type-stat tabular-nums">{value}</div>
      <div className="mt-xs type-caption">{label}</div>
    </div>
  );
}

// Карточка конвейера: где кампания стоит, что мешает её запустить и сама кнопка запуска.
//
// Собрана из `LaunchCard`, которую редизайн разделил надвое: счётчики, готовность и пуск
// остались здесь, а терминал уехал в `LogCard`. Разделение не косметическое — прежняя
// карточка складывала «сколько всего настроено» и «что происходит прямо сейчас» в одну
// колонку, и вторая половина всегда была ниже сгиба.
//
// Хуков, кроме `useTranslation`, нет: каждый запрос живёт на странице, как и у соседей.
export function PipelineCard({
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
  const percent = total === 0 ? 0 : Math.min(100, Math.round((sent / total) * 100));
  // Кампания режима `revive` крутится, пока её не остановят, поэтому сервер не шлёт
  // знаменателя и делить не на что. Счётчик ЗАМЕНЯЕТ полосу, а не стоит рядом с пустой.
  const looping = campaign.mode === 'revive';
  const titleOf = (accountId: string) =>
    pool.find((account) => account.account_id === accountId)?.title ?? accountId;
  const halted = run.halted_accounts ?? [];

  return (
    <Card>
      <div className="mb-2xl flex flex-wrap items-center gap-md">
        {/* Статус — ПОСЛЕ имени, а не перед заголовком: первым в карточке читают, о чём
            она, а плашка перед «Конвейер» отодвигала заголовок от края и отвечала на
            вопрос, который ещё не задан. */}
        <div className="min-w-0 type-card-title">
          {t('neuroshilling.pipeline.title')}
          <span className="text-action-primary"> — {campaign.name}</span>
        </div>
        <CampaignStatusBadge status={status} />
        <div className="flex-1" />
        {live ? (
          <Button variant="danger" disabled={busy || status === 'stopping'} onClick={onStop}>
            {t('neuroshilling.launch.stop')}
          </Button>
        ) : (
          <Button variant="primary" disabled={busy || blockers.length > 0} onClick={onStart}>
            {t('neuroshilling.launch.start')}
          </Button>
        )}
      </div>

      {/* Шесть колонок без зазора: зазор разорвал бы рельсу, а расстояние между
          подписями уже задано самими колонками. */}
      <div className="mb-xl">
        <div className="grid grid-cols-6">
          <Node
            first
            label={t('neuroshilling.pipeline.node.scenario')}
            sub={t(`neuroshilling.pipeline.sub.scenario.${approved ? 'approved' : 'draft'}`)}
            done={approved}
          />
          <Node
            label={t('neuroshilling.pipeline.node.accounts')}
            sub={t('neuroshilling.pipeline.sub.accounts', { staffed, count: roles.length })}
            done={roles.length > 0 && staffed === roles.length}
          />
          <Node
            label={t('neuroshilling.pipeline.node.targets')}
            sub={t('neuroshilling.targetsCount', { count: targets.length })}
            done={targets.length > 0}
          />
          <Node
            label={t('neuroshilling.pipeline.node.intro')}
            sub={t('neuroshilling.pipeline.sub.intro', {
              min: campaign.pause_min_seconds ?? 0,
              max: campaign.pause_max_seconds ?? 0,
            })}
            done={live}
          />
          <Node
            label={t('neuroshilling.pipeline.node.dialogue')}
            sub={t('neuroshilling.pipeline.sub.dialogue', { count: messageSteps })}
            done={live}
          />
          <Node
            last
            label={t('neuroshilling.pipeline.node.listen')}
            sub={t('neuroshilling.pipeline.sub.listen', { count: campaign.listen_minutes ?? 0 })}
            done={run.listening === true}
          />
        </div>
      </div>

      {/* Одна строка вместо списка причин: макет просит сводку, а не перечисление, и
          перечисление уже есть — в сводке замечаний сайдбара, которая для этого и
          заведена. Здесь называется ПЕРВАЯ причина и их число. */}
      {/* Отступ несёт обёртка, а не само уведомление: расстоянием до соседа
          распоряжается родитель — уведомление о том, что стоит под ним, не знает. */}
      <div className="mb-lg">
        {blockers.length > 0 ? (
          <Notice tone="info">
            {t('neuroshilling.pipeline.remaining', { first: blockers[0], count: blockers.length })}
          </Notice>
        ) : (
          <Notice tone="success">{t('neuroshilling.pipeline.ready')}</Notice>
        )}
      </div>

      <div className="mb-lg grid grid-cols-3 divide-line overflow-hidden rounded-lg border border-line sm:grid-cols-5 sm:divide-x">
        <Stat label={t('neuroshilling.launch.tile.accounts')} value={String(roster.length)} />
        <Stat label={t('neuroshilling.launch.tile.targets')} value={String(targets.length)} />
        <Stat label={t('neuroshilling.launch.tile.roles')} value={String(roles.length)} />
        <Stat label={t('neuroshilling.launch.tile.messages')} value={String(messageSteps)} />
        <Stat
          label={t('neuroshilling.launch.tile.dialogue')}
          value={clock(dialogueSeconds(steps))}
        />
      </div>

      {/* `sent` / `total` считают только шаги-СООБЩЕНИЯ: реакции журналируются, но
          пропущенная реакция — не потерянный прогресс, и полоса, считающая каждый шаг,
          врала бы вниз. */}
      <div className="mb-sm flex flex-wrap items-center gap-sm">
        <Badge className="tabular-nums">
          {t('neuroshilling.launch.substitutions', { n: run.substitutions ?? 0 })}
        </Badge>
        <span className="ml-auto type-caption tabular-nums">
          {t(looping ? 'neuroshilling.launch.sentTotal' : 'neuroshilling.launch.progress', {
            sent,
            total,
          })}
        </span>
      </div>
      {looping ? null : (
        <div
          role="progressbar"
          aria-label={t('neuroshilling.launch.progressLabel')}
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={sent}
          className="h-meter w-full overflow-hidden rounded-full bg-canvas"
        >
          <div
            className="h-full rounded-full bg-action-primary transition-[width] duration-reveal"
            style={{ width: `${String(percent)}%` }}
          />
        </div>
      )}

      {/* Показывается только пока прогон действительно читает: три переключателя и так
          лежат в строке кампании, а чего по ним не видно — работает ли сейчас хоть один. */}
      {run.listening === true ? (
        <div className="mt-md flex flex-wrap items-center gap-sm rounded-lg bg-canvas px-md py-sm type-caption tabular-nums">
          <span className="font-medium">{t('neuroshilling.launch.listening')}</span>
          <span>{t('neuroshilling.launch.chatSeen', { n: run.chat_messages_seen ?? 0 })}</span>
          <span>{t('neuroshilling.launch.humanReplies', { n: run.human_replies_sent ?? 0 })}</span>
        </div>
      ) : null}

      {/* Оба уведомления об исходе — в одной колонке с `gap`: расстояние между ними и
          до того, что выше, принадлежит ей, а не им. */}
      {status === 'failed' || halted.length > 0 ? (
        <div className="mt-md flex flex-col gap-md">
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
    </Card>
  );
}
