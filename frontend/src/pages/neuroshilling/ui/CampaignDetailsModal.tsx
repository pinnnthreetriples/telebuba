import type { ColumnDef } from '@tanstack/react-table';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import type {
  NeuroshillingBoardAccount,
  NeuroshillingCampaign,
  NeuroshillingRole,
  NeuroshillingRunStatus,
  NeuroshillingStep,
} from '@/shared/api';
import { Badge, Button, DataTable, type DataTableColumnMeta, Modal } from '@/shared/ui';

import { CampaignStatusBadge } from './CampaignStatusBadge';

// Одна работа: ОДИН аккаунт в ОДНОЙ цели.
interface WorkRow {
  key: string;
  account: string;
  role: string;
  target: string;
  line: string;
  // Тон и подпись состояния. Строкой, а не полем с провода: построчного статуса на
  // проводе НЕТ — см. шапку компонента.
  state: 'halted' | 'busy' | 'unstaffed' | 'live' | 'idle';
}

const STATE_TONE = {
  halted: 'danger',
  busy: 'warning',
  unstaffed: 'warning',
  live: 'success',
  idle: 'neutral',
} as const;

// Подробности кампании: кто, где и что говорит.
//
// Строится из того, что доска действительно отдаёт: ростер (`available` с `assigned`),
// нормализованные цели, роли и шаги сценария, и `run` с остановленными аккаунтами.
// Построчного «отправлено / пропущено» в API НЕТ: журнал по шагам живёт на сервере
// (`NeuroshillingStepKey`, `NeuroshillingMessageStatus`), но в `NeuroshillingBoard` не
// приходит. Поэтому колонка состояния говорит то, что мы знаем на самом деле — вывел ли
// Telegram аккаунт из прогона, держит ли его другая кампания, есть ли у роли исполнитель
// и идёт ли прогон, — и ни одна ячейка не притворяется отчётом об отправке.
export function CampaignDetailsModal({
  campaign,
  pool,
  targets,
  roles,
  steps,
  run,
  onOpenSettings,
  onClose,
}: {
  campaign: NeuroshillingCampaign;
  pool: NeuroshillingBoardAccount[];
  targets: string[];
  roles: NeuroshillingRole[];
  steps: NeuroshillingStep[];
  run: NeuroshillingRunStatus;
  onOpenSettings: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const live = campaign.status === 'running' || campaign.status === 'stopping';

  const rows = useMemo<WorkRow[]>(() => {
    // Множество строится ВНУТРИ мемоизации: снаружи оно пересоздавалось на каждом
    // рендере и, стоя в зависимостях, отменяло её собой — таблица пересобиралась
    // всегда, а `useMemo` только делал вид.
    const halted = new Set(run.halted_accounts ?? []);
    const roster = pool.filter((account) => account.assigned && account.is_reserve !== true);
    const nameOf = new Map(roles.map((role) => [role.role_id, role.name]));
    return roster.flatMap((account) =>
      targets.map((target) => {
        // Первая реплика этого аккаунта: шаг-сообщение его роли, который прозвучит
        // раньше всех. Именно она отвечает на «что он там скажет», а весь диалог целиком
        // читают в утверждении сценария.
        const first = steps.find(
          (step) => step.kind === 'message' && step.role_id === account.role_id,
        );
        return {
          key: `${account.account_id}-${target}`,
          account: account.title,
          role: nameOf.get(account.role_id ?? '') ?? t('neuroshilling.details.noRole'),
          target,
          line: first?.text?.trim() ?? '—',
          state: halted.has(account.account_id)
            ? ('halted' as const)
            : account.busy_owner != null
              ? ('busy' as const)
              : account.role_id == null
                ? ('unstaffed' as const)
                : live
                  ? ('live' as const)
                  : ('idle' as const),
        };
      }),
    );
  }, [pool, targets, roles, steps, run.halted_accounts, live, t]);

  const columns = useMemo<ColumnDef<WorkRow>[]>(
    () => [
      {
        accessorKey: 'account',
        header: t('neuroshilling.details.column.account'),
        meta: { cardSlot: 'title' } satisfies DataTableColumnMeta,
      },
      { accessorKey: 'role', header: t('neuroshilling.details.column.role') },
      {
        accessorKey: 'target',
        header: t('neuroshilling.details.column.target'),
        meta: { cellClassName: 'text-info-strong' } satisfies DataTableColumnMeta,
      },
      {
        accessorKey: 'line',
        header: t('neuroshilling.details.column.line'),
        meta: { cellClassName: 'max-w-name truncate' } satisfies DataTableColumnMeta,
      },
      {
        accessorKey: 'state',
        header: t('neuroshilling.details.column.state'),
        cell: ({ row }) => (
          <Badge dot tone={STATE_TONE[row.original.state]}>
            {t(`neuroshilling.details.state.${row.original.state}`)}
          </Badge>
        ),
      },
    ],
    [t],
  );

  return (
    <Modal onClose={onClose} size="table" label={campaign.name}>
      <div className="flex flex-wrap items-center gap-md border-b border-line-row px-2xl pb-lg pt-xl">
        <div className="min-w-0">
          <div className="truncate type-dialog-title">{campaign.name}</div>
          {campaign.topic ? (
            <div className="mt-hair truncate type-caption">{campaign.topic}</div>
          ) : null}
        </div>
        <CampaignStatusBadge status={campaign.status ?? 'idle'} />
        <div className="flex-1" />
        <span className="type-caption tabular-nums">
          {t('neuroshilling.launch.progress', { sent: run.sent ?? 0, total: run.total ?? 0 })}
        </span>
      </div>

      <div className="px-2xl py-lg">
        {rows.length === 0 ? (
          // Пар «аккаунт × цель» нет, пока нет хотя бы одного из двух, и это не пустая
          // таблица, а незаконченная настройка — поэтому сюда же и кнопка.
          <div className="py-xl text-center type-prose">{t('neuroshilling.details.none')}</div>
        ) : (
          <DataTable data={rows} columns={columns} />
        )}
      </div>

      <div className="flex items-center justify-end gap-sm border-t border-line-row px-2xl py-lg">
        <Button size="sm" onClick={onClose}>
          {t('neuroshilling.details.close')}
        </Button>
        <Button variant="primary" size="sm" onClick={onOpenSettings}>
          {t('neuroshilling.details.settings')}
        </Button>
      </div>
    </Modal>
  );
}
