import { useTranslation } from 'react-i18next';

import type { SurvivalLife } from '@/shared/api';
import { cn } from '@/shared/lib';
import { EmptyState, Icon } from '@/shared/ui';

// One row per account life on a shared day axis (a swimmer plot): the warming stretch
// blue, the life after its first release green, a dot on each wound (a spam block or a
// flood), a tick where a new wave began («Вернуть в прогрев»), a cross on the day of
// death. A written-off row is grey from its release on: it left the count.
const AFTER: Record<SurvivalLife['status'], string> = {
  warming: 'bg-action-primary',
  alive: 'bg-success',
  wounded: 'bg-success',
  unchecked: 'bg-line-strong',
  dead: 'bg-success',
  censored: 'bg-line-strong',
};

function pct(day: number, span: number): string {
  return `${String((Math.min(day, span) / span) * 100)}%`;
}

function LifeBar({ stint, span }: { stint: SurvivalLife; span: number }) {
  const { t } = useTranslation();
  const release = stint.release_day ?? stint.end_day;
  const cause = stint.cause ? t(`warming.survival.causes.${stint.cause}`) : '';
  return (
    <div className="relative h-badge">
      <div className="absolute inset-x-0 top-1/2 h-meter -translate-y-1/2 rounded-full bg-canvas" />
      <div
        className="absolute top-1/2 h-meter -translate-y-1/2 rounded-full bg-action-primary"
        style={{ left: 0, width: pct(release, span) }}
      />
      {stint.release_day == null ? null : (
        <div
          className={cn(
            'absolute top-1/2 h-meter -translate-y-1/2 rounded-full',
            AFTER[stint.status],
          )}
          style={{ left: pct(release, span), width: pct(stint.end_day - release, span) }}
        />
      )}
      {stint.waves
        ?.filter((wave) => wave.wave > 1)
        .map((wave) => (
          <span
            key={`wave-${String(wave.wave)}`}
            title={t('warming.survival.swimmer.wave', { wave: wave.wave, day: wave.start_day })}
            className="absolute top-1/2 h-badge w-0 -translate-y-1/2 border-l border-line-strong"
            style={{ left: pct(wave.start_day, span) }}
          />
        ))}
      {stint.wound_days?.map((day, index) => (
        <span
          key={`${String(day)}-${String(index)}`}
          title={t('warming.survival.swimmer.wound', { day })}
          className="absolute top-1/2 size-dot -translate-x-1/2 -translate-y-1/2 rounded-full bg-warning ring-2 ring-surface-card"
          style={{ left: pct(day, span) }}
        />
      ))}
      {stint.death_day == null ? null : (
        <span
          title={t('warming.survival.swimmer.death', { day: stint.death_day, cause })}
          className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 text-danger"
          style={{ left: pct(stint.death_day, span) }}
        >
          <Icon name="close" size={14} />
        </span>
      )}
    </div>
  );
}

export function SwimmerPlot({ lives }: { lives: readonly SurvivalLife[] }) {
  const { t } = useTranslation();
  if (lives.length === 0) {
    return <EmptyState size="sm">{t('warming.survival.swimmer.empty')}</EmptyState>;
  }
  const span = Math.max(1, ...lives.map((life) => life.end_day));
  return (
    <div>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {lives.map((stint) => {
          const status = t(`warming.survival.status.${stint.status}`);
          return (
            <li
              key={`${stint.account_id}-${stint.started_at}`}
              aria-label={t('warming.survival.swimmer.row', {
                label: stint.label,
                status,
                days: stint.end_day,
              })}
              className="grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)] items-center gap-3"
            >
              <span className="truncate type-small">
                {stint.label} · {status}
              </span>
              <LifeBar stint={stint} span={span} />
            </li>
          );
        })}
      </ul>
      <p className="mt-3 mb-0 type-small">{t('warming.survival.swimmer.legend')}</p>
    </div>
  );
}
