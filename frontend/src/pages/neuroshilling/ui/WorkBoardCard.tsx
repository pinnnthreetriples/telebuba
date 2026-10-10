import type { ColumnDef } from '@tanstack/react-table';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import type { NeuroshillingCampaign, NeuroshillingRunStatus } from '@/shared/api';
import {
  Badge,
  Card,
  CardHeader,
  DataTable,
  type DataTableColumnMeta,
  ProgressBar,
} from '@/shared/ui';

import { CampaignStatusBadge } from './CampaignStatusBadge';
import { countTargets } from './setupDraft';

interface BoardRow {
  campaignId: string;
  name: string;
  topic: string;
  targets: number;
  status: NonNullable<NeuroshillingCampaign['status']>;
  // Прогресс есть ровно у одной строки — у выбранной, и это свойство ДАННЫХ, а не
  // упущение вёрстки: `sent`/`total` живут в `NeuroshillingRunStatus`, который приходит
  // из `getNeuroshillingBoard` по одной кампании за раз. Строка кампании (`status`,
  // `run_id`) счётчиков не несёт, поэтому у остальных строк здесь честный прочерк, а не
  // ноль: ноль означал бы «ничего не отправлено», чего мы не знаем.
  sent: number | null;
  total: number | null;
}

// Доска работ: все кампании одним списком — что в них, насколько они продвинулись и в
// каком они состоянии.
//
// Живёт рядом с конвейером, который показывает ОДНУ кампанию во всех подробностях.
// Разделение труда здесь и есть смысл редизайна: прежде страница показывала только
// выбранную кампанию, и «что вообще происходит» приходилось собирать, переключаясь между
// ними по одной.
export function WorkBoardCard({
  campaignList,
  campaignId,
  run,
  targets,
  onSelect,
}: {
  campaignList: NeuroshillingCampaign[];
  campaignId: string | null;
  run: NeuroshillingRunStatus;
  // Серверный разбор целей ВЫБРАННОЙ кампании. Нужен именно он, а не `countTargets` по
  // всем строкам подряд: клиентский счёт — приблизительный (он считает то, что набрано, а
  // сервер — то, что сохранено и нормализовано), и конвейер стоит на этой же странице,
  // считая по серверному. Две цифры об одной кампании, разные на одном экране, — дефект,
  // которого не было бы, останься доска единственным местом, где цели считают.
  targets: string[];
  // Клик по строке ОТКРЫВАЕТ кампанию: выбирает её и показывает подробности. Выбор без
  // показа был действием, у которого не видно последствия — строка чуть подсвечивалась,
  // и всё.
  onSelect: (campaignId: string) => void;
}) {
  const { t } = useTranslation();

  const rows = useMemo<BoardRow[]>(
    () =>
      campaignList.map((campaign) => {
        const selected = campaign.campaign_id === campaignId;
        return {
          campaignId: campaign.campaign_id,
          name: campaign.name,
          topic: campaign.topic ?? '',
          targets: selected ? targets.length : countTargets(campaign.targets_raw ?? ''),
          status: campaign.status ?? 'idle',
          sent: selected ? (run.sent ?? 0) : null,
          total: selected ? (run.total ?? 0) : null,
        };
      }),
    [campaignList, campaignId, run, targets],
  );

  const running = campaignList.filter(
    (campaign) => campaign.status === 'running' || campaign.status === 'stopping',
  ).length;

  const columns = useMemo<ColumnDef<BoardRow>[]>(
    () => [
      {
        accessorKey: 'name',
        header: t('neuroshilling.board.column.campaign'),
        meta: { cardSlot: 'title' } satisfies DataTableColumnMeta,
        cell: ({ row }) => (
          <>
            <div className="truncate type-body-medium">{row.original.name}</div>
            {row.original.topic ? (
              <div className="truncate type-small">{row.original.topic}</div>
            ) : null}
          </>
        ),
      },
      {
        accessorKey: 'targets',
        header: t('neuroshilling.board.column.volume'),
        meta: { cellClassName: 'tabular-nums' } satisfies DataTableColumnMeta,
        // Число, а не «6 целей»: единицу называет заголовок, и повторять её в каждой
        // ячейке значит сказать одно и то же дважды в одном столбце.
        cell: ({ row }) => row.original.targets,
      },
      {
        id: 'progress',
        header: t('neuroshilling.board.column.progress'),
        cell: ({ row }) => {
          const { sent, total } = row.original;
          if (sent === null || total === null) return <span className="type-small">—</span>;
          return (
            <>
              <ProgressBar value={sent} max={total} className="w-full" />
              <div className="mt-1 type-small tabular-nums">
                {sent}/{total}
              </div>
            </>
          );
        },
      },
      {
        accessorKey: 'status',
        header: t('neuroshilling.board.column.status'),
        cell: ({ row }) => <CampaignStatusBadge status={row.original.status} />,
      },
    ],
    [t],
  );

  return (
    <Card className="py-6">
      <CardHeader
        wrap
        className="mb-3 px-6"
        title={t('neuroshilling.board.title')}
        badge={<Badge className="tabular-nums">{campaignList.length}</Badge>}
      >
        <span className="shrink-0 type-small tabular-nums">
          {t('neuroshilling.board.running', { count: running })}
        </span>
      </CardHeader>
      {/* Пустого состояния здесь нет умышленно: страница не рисует доску, пока кампаний
          нет, а «Пока нет кампаний» уже сказано списком в сайдбаре — том самом, где эту
          пустоту и устраняют кнопкой «Создать кампанию». Второй раз та же фраза только
          отняла бы экран. */}
      <DataTable
        data={rows}
        columns={columns}
        getRowProps={(row) => ({
          className: 'cursor-pointer',
          onClick: () => {
            onSelect(row.original.campaignId);
          },
        })}
      />
    </Card>
  );
}
