import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { warmingSurvivalQueryOptions } from '@/entities/warming';
import { Card, CardHeader, EmptyState, Icon } from '@/shared/ui';

import { CausesList } from './CausesList';
import { type CohortKind, CohortTable } from './CohortTable';
import { DailyHistory } from './DailyHistory';
import { SURVIVAL_POLL_MS } from './SurvivalTile';
import { SwimmerPlot } from './SwimmerPlot';
import { WindowCounters } from './WindowCounters';

function formatDay(day: string, lang: string): string {
  return new Intl.DateTimeFormat(lang, {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  }).format(new Date(`${day}T00:00:00`));
}

// The survival panel the header tile opens: the three counters, a life bar per account
// with a cross on the day of death, the cohort table, causes and the fleet's daily
// history. A smoothed curve waits for ~30 releases (`CURVE_FROM_RELEASES`) and is not
// drawn yet.
export function SurvivalPanel({ id }: { id: string }) {
  const { t, i18n } = useTranslation();
  const [cohort, setCohort] = useState<CohortKind>('week');
  const { data, isPending, isError } = useQuery({
    ...warmingSurvivalQueryOptions({ query: { cohort } }),
    refetchInterval: SURVIVAL_POLL_MS,
    // A new cut keeps the old table on screen until it lands, instead of a blank card.
    placeholderData: keepPreviousData,
  });
  const since = data?.full_data_since;
  return (
    <Card id={id} className="p-4">
      <CardHeader
        className="mb-4"
        title={t('warming.survival.title')}
        icon={<Icon name="chart" size={16} />}
        tone="success"
        subtitle={
          since
            ? t('warming.survival.fullDataSince', { date: formatDay(since, i18n.language) })
            : null
        }
      />
      {isPending ? (
        <EmptyState size="sm">{t('warming.survival.loading')}</EmptyState>
      ) : isError ? (
        <EmptyState size="sm" tone="danger">
          {t('warming.survival.error')}
        </EmptyState>
      ) : (
        <div className="flex flex-col gap-6">
          <WindowCounters report={data} />
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div>
              <div className="mb-3 type-h3">{t('warming.survival.swimmer.title')}</div>
              <SwimmerPlot stints={data.stints ?? []} />
            </div>
            <div className="flex min-w-0 flex-col gap-4">
              <div className="type-h3">{t('warming.survival.cohort.title')}</div>
              <CohortTable report={data} kind={cohort} onKindChange={setCohort} />
              <CausesList causes={data.causes ?? []} />
            </div>
          </div>
          <DailyHistory daily={data.daily ?? []} />
        </div>
      )}
    </Card>
  );
}
