// The masstagging pipeline on the shared `PipelineCard`: the badge, the one main button,
// the stages, the notice and the tiles come from the view model; under the tiles go the
// progress bar (once started), the mode, the "without Premium" count and «Настройки».
import { useTranslation } from 'react-i18next';

import { Badge, Button, HelpHint, Icon, PipelineCard, ProgressBar } from '@/shared/ui';

import type { PipelineView } from '../model/pipeline';

export function MasstaggingPipeline({
  name,
  mode,
  freeAccounts,
  view,
  busy,
  onAction,
  onSettings,
}: {
  name: string;
  mode: 'stories' | 'priming';
  freeAccounts: number;
  view: PipelineView;
  busy: boolean;
  onAction: () => void;
  onSettings: () => void;
}) {
  const { t } = useTranslation();
  const { action, progress } = view;
  return (
    <PipelineCard
      title={
        <>
          {t('masstagging.pipeline.title')}
          <span className="text-action-primary"> — {name}</span>
        </>
      }
      status={<Badge tone={view.badge.tone}>{view.badge.label}</Badge>}
      running={action.kind === 'pause'}
      toggleDisabled={action.disabled || busy}
      startLabel={action.label}
      stopLabel={action.label}
      onToggle={onAction}
      steps={view.steps}
      narrow="list"
      notice={view.notice}
      stats={view.stats}
    >
      <div className="flex flex-col gap-3">
        {view.started ? (
          <ProgressBar
            value={progress.done}
            max={Math.max(1, progress.total)}
            label={t('masstagging.pipeline.progress')}
            className="w-full"
          />
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <Badge>{t(`masstagging.mode.${mode}`)}</Badge>
          {mode === 'stories' && freeAccounts > 0 ? (
            <span className="flex items-center gap-1">
              <Badge tone="warning">{t('masstagging.freeAccounts', { count: freeAccounts })}</Badge>
              <HelpHint
                text={t('masstagging.pipeline.freeHint')}
                example={t('masstagging.pipeline.freeExample')}
              />
            </span>
          ) : null}
          <Button variant="ghost" size="sm" className="ml-auto" onClick={onSettings}>
            <Icon name="pencil" size={14} />
            {t('masstagging.campaign.settings')}
          </Button>
        </div>
      </div>
    </PipelineCard>
  );
}
