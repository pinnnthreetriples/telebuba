import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/shared/ui';

import type { LogEntry } from '@/shared/api';

import { Odometer } from './Odometer';
import { pipelineStage } from './pipelineStage';

const STAGES = ['listen', 'detect', 'filter', 'generate', 'solve', 'comment'] as const;
// Half a cell, in percent of the row: where the first and last dot centres sit.
const railInset = 50 / STAGES.length;

interface Stat {
  label: string;
  value: number;
  color: string;
}

// The engine pipeline card: global start/stop, the six-stage stepper with the
// dual (green/blue) progress fill, a status banner, and the stat odometer grid.
export function PipelineCard({
  running,
  canStart,
  stats,
  events,
  onToggle,
}: {
  running: boolean;
  canStart: boolean;
  stats: Stat[];
  // The page's neurocomment activity log — the rail's real position comes from it.
  events: LogEntry[];
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  // Bumped only to re-read the clock when the current stage goes stale.
  const [, tick] = useState(0);
  // The real pipeline position. `Date.now()` at render (not state) so a stage that
  // arrives after a long idle stretch is measured against now, not against mount.
  const { stage: activeCell, staleAt } = pipelineStage(events, running, Date.now());
  useEffect(() => {
    if (staleAt === null) return;
    const ms = staleAt - Date.now();
    if (ms <= 0) return;
    const id = setTimeout(() => {
      tick((n) => n + 1);
    }, ms);
    return () => {
      clearTimeout(id);
    };
  }, [staleAt]);
  const greenPct = activeCell > 0 ? (activeCell / (STAGES.length - 1)) * 100 : 0;
  const bluePct = activeCell >= 0 ? (activeCell / (STAGES.length - 1)) * 100 : 0;
  return (
    <div className="rounded-card border border-primary-hairline bg-primary-wash px-[18px] py-4 text-ink">
      <div className="mb-[14px] flex flex-wrap items-center justify-between gap-md">
        <div className="flex items-center gap-md">
          <span className="text-lead font-semibold">{t('neurocomment.pipeline.title')}</span>
          <span
            className={`rounded-full px-[10px] py-[3px] text-tiny font-semibold ${running ? 'tb-pulse bg-success-tint text-success-deep' : 'bg-track text-ink-muted'}`}
          >
            {running ? t('neurocomment.pipeline.running') : t('neurocomment.pipeline.stopped')}
          </span>
        </div>
        <Button
          variant="primary"
          size="sm"
          disabled={!running && !canStart}
          onClick={onToggle}
          className={`gap-sm ${running ? 'bg-ink hover:bg-ink' : ''}`}
        >
          {running ? (
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="5" width="4" height="14" rx="1.5" />
              <rect x="14" y="5" width="4" height="14" rx="1.5" />
            </svg>
          ) : (
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
              <path d="M7 5.5v13a1 1 0 0 0 1.5.87l11-6.5a1 1 0 0 0 0-1.74l-11-6.5A1 1 0 0 0 7 5.5z" />
            </svg>
          )}
          {running ? t('neurocomment.runtime.stop') : t('neurocomment.runtime.start')}
        </Button>
      </div>

      {/* Stepper with dual progress fill. Dot and label share ONE cell: as two rows
          they had different geometry — the dots sat in an `mx-2` box while the labels
          spread across the full width in 88px slots — and the ends drifted 29px apart.
          Equal cells put the first and last dot centres half a cell from the edges,
          which is where the rail has to start and stop for the fill below to land on
          a dot. Derived, not a literal: the fills two lines up already read
          `STAGES.length`, and a seventh stage would leave a hardcoded inset behind
          with nothing to fail. */}
      <div className="relative mb-3">
        <div
          className="absolute top-[11px] h-[2px] overflow-hidden rounded-[2px] bg-primary-line"
          style={{ left: `${String(railInset)}%`, right: `${String(railInset)}%` }}
        >
          <div
            className="absolute left-0 top-0 h-full rounded-[2px] bg-success transition-[width] duration-roll ease-out"
            style={{ width: `${String(greenPct)}%` }}
          />
          <div
            className="absolute left-0 top-0 h-full rounded-[2px] bg-primary transition-[width] duration-roll ease-out"
            style={{ width: `${String(bluePct)}%` }}
          />
        </div>
        <div className="relative flex">
          {STAGES.map((stage, index) => (
            <div key={stage} className="flex flex-1 flex-col items-center">
              <div className="flex h-6 w-4 items-center justify-center">
                {index < activeCell ? (
                  <span className="tb-pop flex h-4 w-4 items-center justify-center rounded-full bg-success">
                    <svg
                      width="10"
                      height="10"
                      viewBox="0 0 24 24"
                      fill="none"
                      className="stroke-white"
                      strokeWidth="3.4"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M20 6 9 17l-5-5" />
                    </svg>
                  </span>
                ) : index === activeCell ? (
                  <span className="tb-livedot h-[11px] w-[11px] rounded-full bg-primary" />
                ) : (
                  <span className="h-[9px] w-[9px] rounded-full border-[1.5px] border-primary-line bg-white" />
                )}
              </div>
              {/* No `min-w-0`: the cells then refuse to shrink under the widest label,
                  so the six can never overlap. Hidden below `md`, where they stop
                  contributing that minimum and the single line below names the stage
                  instead — the dots stay at every width and would otherwise mean
                  nothing on their own. */}
              <span
                className={`hidden whitespace-nowrap text-tiny md:block ${
                  index < activeCell
                    ? 'font-medium text-success-deep'
                    : index === activeCell
                      ? 'font-semibold text-primary-deep'
                      : 'text-ink-subtle'
                }`}
              >
                {t(`neurocomment.stage.${stage}`)}
              </span>
            </div>
          ))}
        </div>
      </div>
      {/* Nothing to name while stopped (activeCell -1); the status banner says so. */}
      {activeCell >= 0 ? (
        <div className="mb-3 text-center text-tiny font-semibold text-primary-deep md:hidden">
          {t(`neurocomment.stage.${STAGES[activeCell]}`)}
        </div>
      ) : null}

      <div className="mb-[14px] flex items-center gap-md rounded-lg border border-primary-line bg-primary-tint px-[13px] py-[10px]">
        <span className="pl-pulse h-2 w-2 shrink-0 rounded-full bg-primary" />
        <span className="tb-pulse text-body font-medium text-primary-deep">
          {running
            ? t('neurocomment.pipeline.descRunning')
            : t('neurocomment.pipeline.descStopped')}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-primary-hairline bg-primary-hairline md:grid-cols-6">
        {stats.map((stat) => (
          // Below `md` the tiles pair up, so an ODD count leaves a light-blue hole in the
          // final row from the gap-px/tint border trick — `odd:last:` spans that trailing
          // tile across both columns, and stays right as stats are added or removed.
          <div key={stat.label} className="bg-white px-4 py-[14px] max-md:odd:last:col-span-2">
            <Odometer value={stat.value} tone={stat.color} />
            <div className="mt-[2px] text-tiny text-ink-subtle">{stat.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
