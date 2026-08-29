import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, Icon } from '@/shared/ui';

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
    <div className="rounded-card border border-info-hairline bg-info-tint px-xl py-lg text-content-primary">
      <div className="mb-lg flex flex-wrap items-center justify-between gap-md">
        <div className="flex items-center gap-md">
          <span className="type-card-title">{t('neurocomment.pipeline.title')}</span>
          <span
            className={`rounded-full px-md py-xs text-tiny font-semibold ${running ? 'tb-pulse bg-success-tint text-success-deep' : 'bg-canvas text-content-muted'}`}
          >
            {running ? t('neurocomment.pipeline.running') : t('neurocomment.pipeline.stopped')}
          </span>
        </div>
        <Button
          variant="primary"
          size="sm"
          disabled={!running && !canStart}
          onClick={onToggle}
          className={`gap-sm ${running ? 'bg-content-primary hover:bg-content-primary' : ''}`}
        >
          {running ? <Icon name="pause" size={14} /> : <Icon name="play" size={14} />}
          {running ? t('neurocomment.runtime.stop') : t('neurocomment.runtime.start')}
        </Button>
      </div>

      {/* Stepper with dual progress fill. Dot and label share ONE cell: as two rows
          they had different geometry — the dots sat in an `mx-sm` box while the labels
          spread across the full width in 88px slots — and the ends drifted 29px apart.
          Equal cells put the first and last dot centres half a cell from the edges,
          which is where the rail has to start and stop for the fill below to land on
          a dot. Derived, not a literal: the fills two lines up already read
          `STAGES.length`, and a seventh stage would leave a hardcoded inset behind
          with nothing to fail. */}
      <div className="relative mb-md">
        <div
          className="absolute top-[8px] h-rail overflow-hidden rounded-[2px] bg-info-line"
          style={{ left: `${String(railInset)}%`, right: `${String(railInset)}%` }}
        >
          <div
            className="absolute left-0 top-0 h-full rounded-[2px] bg-success transition-[width] duration-roll ease-out"
            style={{ width: `${String(greenPct)}%` }}
          />
          <div
            className="absolute left-0 top-0 h-full rounded-[2px] bg-action-primary transition-[width] duration-roll ease-out"
            style={{ width: `${String(bluePct)}%` }}
          />
        </div>
        <div className="relative flex">
          {STAGES.map((stage, index) => (
            <div key={stage} className="flex flex-1 flex-col items-center">
              <div className="flex size-glyph items-center justify-center">
                {index < activeCell ? (
                  <span className="tb-pop flex size-spinner items-center justify-center rounded-full bg-success">
                    <Icon name="check" size={10} className="stroke-on-success" />
                  </span>
                ) : index === activeCell ? (
                  <span className="tb-livedot size-node rounded-full bg-action-primary" />
                ) : (
                  <span className="size-node rounded-full border-[1.5px] border-info-line bg-surface-card" />
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
                      ? 'font-semibold text-info-strong'
                      : 'text-content-subtle'
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
        <div className="mb-md text-center type-caption font-semibold text-info-strong md:hidden">
          {t(`neurocomment.stage.${STAGES[activeCell]}`)}
        </div>
      ) : null}

      <div className="mb-lg flex items-center gap-md rounded-lg border border-info-line bg-info-tint px-lg py-md">
        <span className="pl-pulse size-dot shrink-0 rounded-full bg-action-primary" />
        <span className="tb-pulse type-label text-info-strong">
          {running
            ? t('neurocomment.pipeline.descRunning')
            : t('neurocomment.pipeline.descStopped')}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-info-hairline bg-info-hairline md:grid-cols-6">
        {stats.map((stat) => (
          // Below `md` the tiles pair up, so an ODD count leaves a light-blue hole in the
          // final row from the gap-px/tint border trick — `odd:last:` spans that trailing
          // tile across both columns, and stays right as stats are added or removed.
          <div key={stat.label} className="bg-surface-card px-lg py-lg max-md:odd:last:col-span-2">
            <Odometer value={stat.value} tone={stat.color} />
            <div className="mt-hair type-caption">{stat.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
