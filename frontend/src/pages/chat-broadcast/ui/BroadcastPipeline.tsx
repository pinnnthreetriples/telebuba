// The broadcast's pipeline on the shared `PipelineCard`: the badge, the one main button,
// five nodes, the notice and the counters come from the view model; what only the
// broadcast has — the chips, the progress over everything it means to send, the notes
// about halted and busy accounts — goes under the tiles.
//
// The button is the card's start/stop toggle. Start, Resume and Restart are one button
// with three labels: the model picks the label, the card only needs to know it is not Stop.
import { useTranslation } from 'react-i18next';

import { Badge, Notice, PipelineCard, ProgressBar, type StepperStep } from '@/shared/ui';

import type { PipelineView } from '../model/pipeline';

export function BroadcastPipeline({
  name,
  view,
  busy,
  onAction,
}: {
  name: string;
  view: PipelineView;
  busy: boolean;
  onAction: () => void;
}) {
  const { t } = useTranslation();
  const { progress, action } = view;
  const steps: StepperStep[] = view.nodes.map((node) => ({
    id: node.id,
    label: node.label,
    caption: node.sub,
    state: node.state,
  }));
  const under =
    progress !== null || view.extras.length > 0 ? (
      <div className="flex flex-col gap-3">
        {progress === null ? null : (
          <div>
            <div className="mb-2 flex flex-wrap items-center gap-2">
              {view.chips.map((chip) => (
                <Badge key={chip.label} tone={chip.tone} className="tabular-nums">
                  {chip.label}
                </Badge>
              ))}
              <span className="ml-auto type-small tabular-nums">
                {progress.total === null
                  ? t('chatBroadcast.pipeline.progressEndless', { sent: progress.sent })
                  : t('chatBroadcast.pipeline.progress', {
                      sent: progress.sent,
                      total: progress.total,
                    })}
              </span>
            </div>
            {progress.total === null || progress.total === 0 ? null : (
              <ProgressBar value={progress.sent} max={progress.total} className="w-full" />
            )}
          </div>
        )}
        {view.extras.map((extra) => (
          // The card itself is `info-tint`: an info note without its line would be text
          // floating on the card, so only that tone keeps the border.
          <Notice key={extra.text} tone={extra.tone} bordered={extra.tone === 'info'}>
            {extra.text}
          </Notice>
        ))}
      </div>
    ) : undefined;
  return (
    <PipelineCard
      title={
        <>
          {t('chatBroadcast.pipeline.title')}
          <span className="text-action-primary"> — {name}</span>
        </>
      }
      status={<Badge tone={view.badge.tone}>{view.badge.label}</Badge>}
      running={action.kind === 'stop'}
      toggleDisabled={action.disabled || busy}
      startLabel={action.label}
      stopLabel={action.label}
      onToggle={onAction}
      steps={steps}
      narrow="list"
      notice={view.notice}
      stats={view.stats.map((stat) => ({ label: stat.label, value: stat.value }))}
    >
      {under}
    </PipelineCard>
  );
}
