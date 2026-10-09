// The sidebar: the broadcasts (select, create, start/stop, settings, delete) and the
// "how it works" card — the neuroshilling sidebar, row for row.
import { useTranslation } from 'react-i18next';

import type { ChatBroadcastCampaign } from '@/shared/api';
import type { BadgeTone } from '@/shared/ui';
import {
  Badge,
  Button,
  CollapsibleCard,
  Icon,
  IconButton,
  Input,
  HowItWorksCard,
  SelectableCard,
} from '@/shared/ui';

const TONE: Record<ChatBroadcastCampaign['status'], BadgeTone> = {
  draft: 'neutral',
  running: 'info',
  stopping: 'neutral',
  stopped: 'neutral',
  done: 'success',
  failed: 'danger',
  stalled: 'danger',
};

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
  campaigns: ChatBroadcastCampaign[];
  selectedId: string | null;
  openActions: string | null;
  creating: boolean;
  createName: string;
  onSelect: (id: string) => void;
  onToggleActions: (id: string) => void;
  onToggleRun: (campaign: ChatBroadcastCampaign) => void;
  onSettings: (id: string) => void;
  onDelete: (campaign: ChatBroadcastCampaign) => void;
  onStartCreate: () => void;
  onCancelCreate: () => void;
  onCreateName: (value: string) => void;
  onCreate: () => void;
}) {
  const { t } = useTranslation();
  return (
    <CollapsibleCard
      defaultOpen
      label={t('chatBroadcast.campaigns.title')}
      headerClassName="px-4 py-3"
      bodyClassName="px-4 pb-4"
      title={t('chatBroadcast.campaigns.title')}
    >
      <div className="flex flex-col gap-2">
        {campaigns.map((campaign) => {
          const running = campaign.status === 'running' || campaign.status === 'stopping';
          const accounts = t('chatBroadcast.campaigns.accounts', { count: campaign.account_count });
          const meta =
            campaign.target_mode === 'own'
              ? t('chatBroadcast.campaigns.metaOwn', { accounts })
              : t('chatBroadcast.campaigns.meta', {
                  chats: t('chatBroadcast.campaigns.chats', { count: campaign.target_count }),
                  accounts,
                });
          return (
            <SelectableCard
              key={campaign.campaign_id}
              surfaceId={`cb-camp-${campaign.campaign_id}`}
              name={campaign.name}
              meta={meta}
              status={
                <Badge tone={TONE[campaign.status]}>
                  {t(`chatBroadcast.phase.${campaign.status}`)}
                </Badge>
              }
              selected={campaign.campaign_id === selectedId}
              actionsOpen={openActions === campaign.campaign_id}
              actionsLabel={t('chatBroadcast.campaign.actions')}
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
                    title={
                      running ? t('chatBroadcast.campaign.pause') : t('chatBroadcast.campaign.run')
                    }
                    aria-label={
                      running ? t('chatBroadcast.campaign.pause') : t('chatBroadcast.campaign.run')
                    }
                    className={
                      running
                        ? 'text-warning-deep hover:bg-warning-tint'
                        : 'text-success-deep hover:bg-success-tint'
                    }
                    onClick={() => {
                      onToggleRun(campaign);
                    }}
                  >
                    <Icon name={running ? 'pause' : 'play'} size={16} />
                  </IconButton>
                  <IconButton
                    size="md"
                    tone="primary"
                    title={t('chatBroadcast.campaign.settings')}
                    aria-label={t('chatBroadcast.campaign.settings')}
                    onClick={() => {
                      onSettings(campaign.campaign_id);
                    }}
                  >
                    <Icon name="pencil" size={16} />
                  </IconButton>
                  <IconButton
                    size="md"
                    tone="danger"
                    title={t('chatBroadcast.campaign.delete')}
                    aria-label={t('chatBroadcast.campaign.delete')}
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
            {t('chatBroadcast.campaigns.none')}
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
            placeholder={t('chatBroadcast.campaigns.namePlaceholder')}
            aria-label={t('chatBroadcast.campaigns.namePlaceholder')}
            className="min-w-0 flex-1 border-action-primary"
          />
          <Button variant="primary" size="sm" disabled={!createName.trim()} onClick={onCreate}>
            {t('chatBroadcast.campaigns.confirm')}
          </Button>
          <IconButton
            size="sm"
            title={t('chatBroadcast.campaigns.cancel')}
            aria-label={t('chatBroadcast.campaigns.cancel')}
            onClick={onCancelCreate}
          >
            <Icon name="close" size={14} />
          </IconButton>
        </div>
      ) : (
        <Button variant="dashed" fullWidth className="mt-2 font-medium" onClick={onStartCreate}>
          {t('chatBroadcast.campaigns.create')}
        </Button>
      )}
    </CollapsibleCard>
  );
}

const HOW_STEPS = ['step1', 'step2', 'step3', 'step4'] as const;

export function BroadcastHowItWorks() {
  const { t } = useTranslation();
  return (
    <HowItWorksCard
      defaultOpen
      title={t('chatBroadcast.howto.title')}
      steps={HOW_STEPS.map((step) => t(`chatBroadcast.howto.${step}`))}
    />
  );
}
