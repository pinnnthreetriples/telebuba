// The pipeline: the campaign's badge and its one main button, five nodes, the notice,
// the counters, and the progress over everything the run is meant to send.
import { useTranslation } from 'react-i18next';

import { Badge, Button, Card, Notice, ProgressBar } from '@/shared/ui';

import type { PipelineView } from '../model/pipeline';

function Node({
  label,
  sub,
  done,
  first,
  last,
}: {
  label: string;
  sub: string;
  done: boolean;
  first: boolean;
  last: boolean;
}) {
  return (
    <div className="flex min-w-0 items-center gap-3 text-left sm:flex-col sm:gap-2 sm:text-center">
      <div className="flex w-auto shrink-0 items-center sm:w-full">
        <span className={`hidden h-rail flex-1 bg-line sm:block ${first ? 'invisible' : ''}`} />
        <span
          className={`size-node shrink-0 rounded-full border-2 ${done ? 'border-action-primary bg-action-primary' : 'border-line-strong bg-surface-card'}`}
        />
        <span className={`hidden h-rail flex-1 bg-line sm:block ${last ? 'invisible' : ''}`} />
      </div>
      <div className="flex min-w-0 flex-1 flex-col sm:flex-none">
        <span className={done ? 'type-body-medium' : 'type-small'}>{label}</span>
        <span className="-mt-1 type-small">{sub}</span>
      </div>
    </div>
  );
}

export function BroadcastPipelineCard({
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
  const { progress } = view;
  return (
    <Card>
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <div className="min-w-0 type-h3">
          {t('chatBroadcast.pipeline.title')}
          <span className="text-action-primary"> — {name}</span>
        </div>
        <Badge tone={view.badge.tone}>{view.badge.label}</Badge>
        <div className="flex-1" />
        <Button
          variant={view.action.variant}
          disabled={view.action.disabled || busy}
          onClick={onAction}
        >
          {view.action.label}
        </Button>
      </div>
      <div className="mb-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-5 sm:gap-0">
          {view.nodes.map((node, index) => (
            <Node
              key={node.id}
              first={index === 0}
              last={index === view.nodes.length - 1}
              label={node.label}
              sub={node.sub}
              done={node.done}
            />
          ))}
        </div>
      </div>
      <div className="mb-4">
        <Notice tone={view.notice.tone}>{view.notice.text}</Notice>
      </div>
      <div className="mb-4 grid grid-cols-3 divide-line overflow-hidden rounded-lg border border-line sm:grid-cols-5 sm:divide-x">
        {view.stats.map((stat) => (
          <div key={stat.id} className="px-3 py-3">
            <div className="type-h1 tabular-nums">{stat.value}</div>
            <div className="mt-1 type-small">{stat.label}</div>
          </div>
        ))}
      </div>
      {progress === null ? null : (
        <>
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
        </>
      )}
      {view.extras.length === 0 ? null : (
        <div className="mt-3 flex flex-col gap-3">
          {view.extras.map((extra) => (
            <Notice key={extra.text} tone={extra.tone} bordered={false}>
              {extra.text}
            </Notice>
          ))}
        </div>
      )}
    </Card>
  );
}
