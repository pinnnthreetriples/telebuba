import { useTranslation } from 'react-i18next';

import type { FleetDailyStats } from '@/shared/api';
import { cn } from '@/shared/lib';
import { EmptyState } from '@/shared/ui';

type Metric = 'cycles' | 'floods';

const FILL: Record<Metric, string> = {
  cycles: 'bg-action-primary',
  floods: 'bg-warning',
};

function shortDay(day: string, lang: string): string {
  return new Intl.DateTimeFormat(lang, { day: 'numeric', month: 'numeric' }).format(
    new Date(`${day}T00:00:00`),
  );
}

// One column per day, scaled to the busiest day of the window. The columns stand in a
// `h-header` box: the chart is a strip under the panel, not a figure of its own.
function DayColumns({ daily, metric }: { daily: readonly FleetDailyStats[]; metric: Metric }) {
  const { t, i18n } = useTranslation();
  const peak = Math.max(1, ...daily.map((point) => point[metric] ?? 0));
  return (
    <div>
      <div className="type-small-medium">{t(`warming.survival.daily.${metric}`)}</div>
      <div className="mt-1 flex h-header items-end gap-1">
        {daily.map((point) => {
          const value = point[metric] ?? 0;
          const label = t('warming.survival.daily.bar', {
            day: shortDay(point.day, i18n.language),
            value,
          });
          return (
            <span
              key={point.day}
              title={label}
              aria-label={label}
              role="img"
              className={cn('min-w-0 flex-1 rounded-[2px]', value > 0 ? FILL[metric] : 'bg-line')}
              style={{ height: `${String(Math.max(value / peak, 0.04) * 100)}%` }}
            />
          );
        })}
      </div>
    </div>
  );
}

// The fleet's daily history (stage 1.2), summed over every account: how much warming
// work landed each day, and how many floods it drew.
export function DailyHistory({ daily }: { daily: readonly FleetDailyStats[] }) {
  const { t } = useTranslation();
  return (
    <div>
      <div className="type-h3">{t('warming.survival.daily.title')}</div>
      {daily.length === 0 ? (
        <EmptyState size="sm">{t('warming.survival.daily.empty')}</EmptyState>
      ) : (
        <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2">
          <DayColumns daily={daily} metric="cycles" />
          <DayColumns daily={daily} metric="floods" />
        </div>
      )}
    </div>
  );
}
