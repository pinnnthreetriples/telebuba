// The pipeline: the campaign's badge and its one main button, five nodes, the notice,
// the counters, and the progress over everything the run is meant to send.
import { useTranslation } from 'react-i18next';

import { BAR_FILL, BAR_TRACK } from '@/shared/design-system';
import { Badge, Button, Card, Notice } from '@/shared/ui';

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
    <div className="flex min-w-0 items-center gap-md text-left sm:flex-col sm:gap-sm sm:text-center">
      <div className="flex w-auto shrink-0 items-center sm:w-full">
        <span className={`hidden h-rail flex-1 bg-line sm:block ${first ? 'invisible' : ''}`} />
        <span
          className={`size-node shrink-0 rounded-full border-2 ${done ? 'border-action-primary bg-action-primary' : 'border-line-strong bg-surface-card'}`}
        />
        <span className={`hidden h-rail flex-1 bg-line sm:block ${last ? 'invisible' : ''}`} />
      </div>
      <div className="flex min-w-0 flex-1 flex-col sm:flex-none">
        <span className={done ? 'type-item-title' : 'type-caption'}>{label}</span>
        <span className="-mt-xs type-caption">{sub}</span>
      </div>
    </div>
  );
}

export function PipelineCard({
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
      <div className="mb-2xl flex flex-wrap items-center gap-md">
        <div className="min-w-0 type-card-title">
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
      <div className="mb-xl">
        <div className="grid grid-cols-1 gap-md sm:grid-cols-5 sm:gap-0">
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
      <div className="mb-lg">
        <Notice tone={view.notice.tone}>{view.notice.text}</Notice>
      </div>
      <div className="mb-lg grid grid-cols-3 divide-line overflow-hidden rounded-lg border border-line sm:grid-cols-5 sm:divide-x">
        {view.stats.map((stat) => (
          <div key={stat.id} className="px-md py-md">
            <div className="type-stat tabular-nums">{stat.value}</div>
            <div className="mt-xs type-caption">{stat.label}</div>
          </div>
        ))}
      </div>
      {progress === null ? null : (
        <>
          <div className="mb-sm flex flex-wrap items-center gap-sm">
            {view.chips.map((chip) => (
              <Badge key={chip.label} tone={chip.tone} className="tabular-nums">
                {chip.label}
              </Badge>
            ))}
            <span className="ml-auto type-caption tabular-nums">
              {progress.total === null
                ? t('chatBroadcast.pipeline.progressEndless', { sent: progress.sent })
                : t('chatBroadcast.pipeline.progress', {
                    sent: progress.sent,
                    total: progress.total,
                  })}
            </span>
          </div>
          {progress.total === null || progress.total === 0 ? null : (
            <div className={`${BAR_TRACK} w-full`}>
              <div
                className={`${BAR_FILL} bg-action-primary`}
                style={{
                  width: `${String(Math.min(100, Math.round((progress.sent / progress.total) * 100)))}%`,
                }}
              />
            </div>
          )}
        </>
      )}
      {view.extras.length === 0 ? null : (
        <div className="mt-md flex flex-col gap-md">
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
