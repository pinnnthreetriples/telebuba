import type { ColumnDef } from '@tanstack/react-table';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import type { SurvivalCohort, SurvivalReport } from '@/shared/api';
import { DataTable, EmptyState, SegmentedControl } from '@/shared/ui';

import { counterOf, isoWeekNumber, shareOf, type SurvivalWindow } from '../lib/share';

export type CohortKind = SurvivalReport['cohort'];

const KINDS: readonly CohortKind[] = ['week', 'persona', 'length', 'proxy_country'];
const WINDOWS: readonly SurvivalWindow[] = ['warming', 'd7', 'd30'];

function cohortLabel(t: TFunction, kind: CohortKind, key: string | null | undefined): string {
  if (key == null) return t('warming.survival.cohort.unknown');
  if (kind === 'week') return t('warming.survival.cohort.weekKey', { week: isoWeekNumber(key) });
  if (kind === 'length') return t('warming.survival.cohort.lengthKey', { days: key });
  if (kind === 'persona') return t(`warming.persona.${key}.name`, { defaultValue: key });
  return key;
}

// «нед. 37 · 3 из 4»: one row per cohort, a «N из M» cell per window. Q10: the start
// week is the default cut; persona, length and proxy country are one click away.
export function CohortTable({
  report,
  kind,
  onKindChange,
}: {
  report: SurvivalReport;
  kind: CohortKind;
  onKindChange: (kind: CohortKind) => void;
}) {
  const { t } = useTranslation();
  const columns: ColumnDef<SurvivalCohort>[] = [
    {
      id: 'cohort',
      header: t('warming.survival.cohort.column'),
      cell: ({ row }) => cohortLabel(t, kind, row.original.key),
      meta: { cardSlot: 'title' },
    },
    ...WINDOWS.map((window): ColumnDef<SurvivalCohort> => ({
      id: window,
      header: t(`warming.survival.window.${window}`),
      cell: ({ row }) => {
        const share = shareOf(counterOf(row.original, window));
        return t('warming.survival.ofTotal', { alive: share.alive, total: share.decided });
      },
      meta: { className: 'text-right', cellClassName: 'text-right tabular-nums' },
    })),
  ];
  const cohorts = report.cohorts ?? [];
  return (
    <div className="flex flex-col gap-3">
      <SegmentedControl
        variant="outline"
        ariaLabel={t('warming.survival.cohort.label')}
        value={kind}
        onChange={onKindChange}
        options={KINDS.map((value) => ({
          value,
          label: t(`warming.survival.cohort.${value}`),
        }))}
      />
      {cohorts.length === 0 ? (
        <EmptyState size="sm">{t('warming.survival.cohort.empty')}</EmptyState>
      ) : (
        <DataTable
          data={cohorts}
          columns={columns}
          getRowId={(cohort) => cohort.key ?? '__unknown'}
        />
      )}
    </div>
  );
}
