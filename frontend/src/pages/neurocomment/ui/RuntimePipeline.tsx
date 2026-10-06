import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { LogEntry } from '@/shared/api';
import { Badge, PipelineCard, type Stat, type StepperStep } from '@/shared/ui';

import { pipelineStage } from './pipelineStage';

const STAGES = ['listen', 'detect', 'filter', 'generate', 'solve', 'comment'] as const;

// The engine pipeline: global start/stop, the six-stage stepper placed by the real
// activity log, a status line and the stat odometer grid. The card itself is the shared
// `PipelineCard`; what is the neurocomment's own is where the rail stands.
export function RuntimePipeline({
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
  // Nothing current while stopped (activeCell -1): every stage is upcoming and the
  // status line says why.
  const steps: StepperStep[] = STAGES.map((stage, index) => ({
    id: stage,
    label: t(`neurocomment.stage.${stage}`),
    state: index < activeCell ? 'done' : index === activeCell ? 'current' : 'upcoming',
  }));
  return (
    <PipelineCard
      title={t('neurocomment.pipeline.title')}
      status={
        <Badge size="sm" tone={running ? 'success' : 'neutral'}>
          {running ? t('neurocomment.pipeline.running') : t('neurocomment.pipeline.stopped')}
        </Badge>
      }
      running={running}
      toggleDisabled={!running && !canStart}
      startLabel={t('neurocomment.runtime.start')}
      stopLabel={t('neurocomment.runtime.stop')}
      onToggle={onToggle}
      steps={steps}
      narrow="current"
      notice={{
        tone: 'info',
        text: running
          ? t('neurocomment.pipeline.descRunning')
          : t('neurocomment.pipeline.descStopped'),
      }}
      stats={stats}
    />
  );
}
