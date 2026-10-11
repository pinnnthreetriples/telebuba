// The sidebar: the campaigns (select, create, start/pause, settings, delete) and the folded
// «Как это работает» — the chat broadcast's sidebar, row for row.
import { useTranslation } from 'react-i18next';

import type { MasstaggingCampaign } from '@/shared/api';
import {
  Badge,
  Button,
  CollapsibleCard,
  HowItWorksCard,
  Icon,
  IconButton,
  Input,
  SelectableCard,
} from '@/shared/ui';

import { STATUS_TONE } from '../model/pipeline';

const GOING: MasstaggingCampaign['status'][] = ['running', 'resting'];

export function CampaignList({
  campaigns,
  selectedId,
  openActions,
  creating,
  createName,
  onSelect,
  onToggleActions,
  onToggleRun,
  onSettings,
  onDelete,
  onStartCreate,
  onCancelCreate,
  onCreateName,
  onCreate,
}: {
  campaigns: MasstaggingCampaign[];
  selectedId: string | null;
  openActions: string | null;
  creating: boolean;
  createName: string;
  onSelect: (id: string) => void;
  onToggleActions: (id: string) => void;
  onToggleRun: (campaign: MasstaggingCampaign) => void;
  onSettings: (id: string) => void;
  onDelete: (campaign: MasstaggingCampaign) => void;
  onStartCreate: () => void;
  onCancelCreate: () => void;
  onCreateName: (value: string) => void;
  onCreate: () => void;
}) {
  const { t } = useTranslation();
  return (
    <CollapsibleCard
      defaultOpen
      label={t('masstagging.campaigns.title')}
      headerClassName="px-4 py-3"
      bodyClassName="px-4 pb-4"
      title={t('masstagging.campaigns.title')}
    >
      <div className="flex flex-col gap-2">
        {campaigns.map((campaign) => {
          const going = GOING.includes(campaign.status);
          const meta = t('masstagging.campaigns.meta', {
            mode: t(`masstagging.mode.${campaign.mode}`),
            targets:
              campaign.target_count > 0
                ? t('masstagging.campaigns.targets', { count: campaign.target_count })
                : t('masstagging.campaigns.noTargets'),
          });
          const runLabel = going ? t('masstagging.campaign.pause') : t('masstagging.campaign.run');
          return (
            <SelectableCard
              key={campaign.campaign_id}
              surfaceId={`mt-camp-${campaign.campaign_id}`}
              name={campaign.name}
              meta={meta}
              status={
                <Badge tone={STATUS_TONE[campaign.status]}>
                  {t(`masstagging.status.${campaign.status}`)}
                </Badge>
              }
              selected={campaign.campaign_id === selectedId}
              actionsOpen={openActions === campaign.campaign_id}
              actionsLabel={t('masstagging.campaign.actions')}
              onSelect={() => {
                onSelect(campaign.campaign_id);
              }}
              onToggleActions={() => {
                onToggleActions(campaign.campaign_id);
              }}
              actions={
                <>
                  <IconButton
                    size="md"
                    tone="neutral"
                    title={runLabel}
                    aria-label={runLabel}
                    disabled={campaign.status === 'draft'}
                    className={
                      going
                        ? 'text-warning-deep hover:bg-warning-tint'
                        : 'text-success-deep hover:bg-success-tint'
                    }
                    onClick={() => {
                      onToggleRun(campaign);
                    }}
                  >
                    <Icon name={going ? 'pause' : 'play'} size={16} />
                  </IconButton>
                  <IconButton
                    size="md"
                    tone="primary"
                    title={t('masstagging.campaign.settings')}
                    aria-label={t('masstagging.campaign.settings')}
                    onClick={() => {
                      onSettings(campaign.campaign_id);
                    }}
                  >
                    <Icon name="pencil" size={16} />
                  </IconButton>
                  <IconButton
                    size="md"
                    tone="danger"
                    title={t('masstagging.campaign.delete')}
                    aria-label={t('masstagging.campaign.delete')}
                    onClick={() => {
                      onDelete(campaign);
                    }}
                  >
                    <Icon name="trash" size={16} />
                  </IconButton>
                </>
              }
            />
          );
        })}
        {campaigns.length === 0 ? (
          <div className="py-4 text-center type-body text-content-subtle">
            {t('masstagging.campaigns.none')}
          </div>
        ) : null}
      </div>
      {creating ? (
        <div className="mt-2 flex items-center gap-2">
          <Input
            size="sm"
            autoFocus
            value={createName}
            onChange={(event) => {
              onCreateName(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && createName.trim()) onCreate();
              if (event.key === 'Escape') onCancelCreate();
            }}
            placeholder={t('masstagging.campaigns.namePlaceholder')}
            aria-label={t('masstagging.campaigns.namePlaceholder')}
            className="min-w-0 flex-1 border-action-primary"
          />
          <Button variant="primary" size="sm" disabled={!createName.trim()} onClick={onCreate}>
            {t('masstagging.campaigns.confirm')}
          </Button>
          <IconButton
            size="sm"
            title={t('masstagging.campaigns.cancel')}
            aria-label={t('masstagging.campaigns.cancel')}
            onClick={onCancelCreate}
          >
            <Icon name="close" size={14} />
          </IconButton>
        </div>
      ) : (
        <Button variant="dashed" fullWidth className="mt-2 font-medium" onClick={onStartCreate}>
          {t('masstagging.campaigns.create')}
        </Button>
      )}
    </CollapsibleCard>
  );
}

const HOW_STEPS = ['step1', 'step2', 'step3', 'step4'] as const;

export function MasstaggingHowItWorks() {
  const { t } = useTranslation();
  return (
    <HowItWorksCard
      title={t('masstagging.howto.title')}
      steps={HOW_STEPS.map((step) => t(`masstagging.howto.${step}`))}
    />
  );
}
