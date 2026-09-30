import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import type { NeurocommentCampaign } from '@/shared/api';
import { type FeedbackResult } from '@/shared/lib';
import {
  Button,
  ChipAddButton,
  CollapsibleCard,
  FeedbackMark,
  Icon,
  IconButton,
  InlineChipEditor,
  SelectableCard,
} from '@/shared/ui';

// Tone is the token the status MEANS (running = success, held = amber, shelved =
// muted), so the pill can't drift from the rest of the design system.
const STATUS_TONE = {
  active: 'text-success-deep',
  paused: 'text-warning-deep',
  archived: 'text-content-muted',
} as const;

// Channel-chip tone driven by the live "Проверить каналы" verdict: banned = red
// (persists), ok = green (5s flash), default = the neutral gray pill.
const CHANNEL_CHIP = {
  banned: 'border-danger bg-danger-tint text-danger-deep',
  ok: 'border-success bg-success-tint text-success-deep',
  default: 'border-line bg-canvas text-content-secondary',
} as const;

// The campaigns card: per-campaign run/pause/edit/delete (SurfHover-revealed),
// the create button, and the selected campaign's channel editor.
export function CampaignsCard({
  campaignList,
  campaignId,
  activeCampaign,
  boardChannels,
  openCampaignActions,
  onToggleActions,
  onSelect,
  onToggleStatus,
  onEditPrompt,
  onDelete,
  onCreate,
  channelFeedback,
  addingChannel,
  onStartAdd,
  onCancelAdd,
  channelInput,
  onChannelInput,
  onAddChannel,
  onRemoveChannel,
  onCheckChannels,
  checkingChannels,
  channelCheckStatus,
  discoverySlot,
}: {
  campaignList: NeurocommentCampaign[];
  campaignId: string | null;
  activeCampaign: NeurocommentCampaign | null;
  boardChannels: { channel: string; deleted_recent?: number }[];
  openCampaignActions: string | null;
  onToggleActions: (campaignId: string) => void;
  onSelect: (campaignId: string) => void;
  onToggleStatus: (campaign: NeurocommentCampaign) => void;
  onEditPrompt: (campaign: NeurocommentCampaign) => void;
  onDelete: (campaign: NeurocommentCampaign) => void;
  onCreate: () => void;
  channelFeedback: Record<string, FeedbackResult>;
  addingChannel: boolean;
  onStartAdd: () => void;
  onCancelAdd: () => void;
  channelInput: string;
  onChannelInput: (value: string) => void;
  onAddChannel: () => void;
  onRemoveChannel: (channel: string) => void;
  onCheckChannels: () => void;
  checkingChannels: boolean;
  channelCheckStatus: Record<string, 'banned' | 'ok'>;
  // Rendered beside "Проверить каналы". A slot, not new state: this component stays
  // purely presentational (zero hooks) while the feature owns its own server I/O.
  discoverySlot?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <CollapsibleCard
      defaultOpen
      label={t('neurocomment.campaigns.title')}
      headerClassName="px-lg py-lg"
      bodyClassName="px-lg pb-lg"
      header={<span className="type-card-title">{t('neurocomment.campaigns.title')}</span>}
    >
      <div className="flex flex-col gap-sm">
        {campaignList.map((campaign) => {
          const isSelected = campaign.campaign_id === campaignId;
          // Per-campaign run state comes from the campaign's own status,
          // not the global engine (finding #2).
          const isRunning = campaign.status === 'active';
          const tone = STATUS_TONE[campaign.status];
          return (
            <SelectableCard
              key={campaign.campaign_id}
              surfaceId={`camp-surf-${campaign.campaign_id}`}
              name={campaign.name}
              meta={t('neurocomment.campaign.meta', {
                channels: campaign.channel_count ?? 0,
                accounts: campaign.account_count ?? 0,
              })}
              status={
                <span
                  className={`inline-flex items-center gap-tight type-caption font-medium ${tone}`}
                >
                  <span className="size-dot rounded-full bg-current" />
                  {t(`neurocomment.campaign.status.${campaign.status}`)}
                </span>
              }
              selected={isSelected}
              actionsOpen={openCampaignActions === campaign.campaign_id}
              actionsLabel={t('neurocomment.campaign.actions')}
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
                    aria-label={
                      isRunning ? t('neurocomment.campaign.pause') : t('neurocomment.campaign.run')
                    }
                    title={
                      isRunning ? t('neurocomment.campaign.pause') : t('neurocomment.campaign.run')
                    }
                    onClick={() => {
                      onToggleStatus(campaign);
                    }}
                    className={
                      isRunning
                        ? 'text-warning-deep hover:bg-warning-tint'
                        : 'text-success-deep hover:bg-success-tint'
                    }
                  >
                    {isRunning ? <Icon name="pause" size={16} /> : <Icon name="play" size={16} />}
                  </IconButton>
                  <IconButton
                    size="md"
                    tone="primary"
                    aria-label={t('neurocomment.campaign.editPrompt')}
                    title={t('neurocomment.campaign.editPrompt')}
                    onClick={() => {
                      // Selecting the campaign too keeps the board query (and thus the
                      // prompt modal's account list) on THIS campaign (finding #5).
                      onEditPrompt(campaign);
                    }}
                  >
                    <Icon name="pencil" size={16} />
                  </IconButton>
                  <IconButton
                    size="md"
                    tone="danger"
                    aria-label={t('neurocomment.campaign.delete')}
                    title={t('neurocomment.campaign.delete')}
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
        {campaignList.length === 0 ? (
          <div className="py-xl text-center type-prose">{t('neurocomment.campaigns.none')}</div>
        ) : null}
      </div>

      <Button variant="dashed" fullWidth className="mt-md font-medium" onClick={onCreate}>
        {t('neurocomment.campaigns.create')}
      </Button>

      {/* campaign channels */}
      <div className="mt-lg border-t border-line-row pt-md">
        <CollapsibleCard
          defaultOpen
          wrapperClassName=""
          headerClassName="px-0 py-0"
          bodyClassName="px-0 pb-0 pt-md"
          label={t('neurocomment.channels.title')}
          header={<span className="type-item-title">{t('neurocomment.channels.title')}</span>}
        >
          <div className="mb-md flex items-center justify-between gap-sm">
            <span className="min-w-0 truncate type-caption font-medium text-action-primary">
              {activeCampaign?.name ?? ''}
            </span>
            <div className="flex shrink-0 items-center gap-sm">
              {discoverySlot}
              <Button
                size="xs"
                disabled={campaignId === null}
                loading={checkingChannels}
                onClick={onCheckChannels}
                // `text-tiny` — см. `ChannelDiscoveryButton`: пара стоит в узкой колонке
                // рядом с именем кампании, и на рунге контрола имя не остаётся.
                className="text-tiny text-content-muted hover:border-action-primary hover:text-action-primary"
              >
                {checkingChannels
                  ? t('neurocomment.channels.checking')
                  : t('neurocomment.channels.check')}
              </Button>
            </div>
          </div>
          <div className="flex flex-wrap items-start gap-sm">
            {boardChannels.map((channel) => (
              <span
                key={channel.channel}
                className={`inline-flex items-center gap-sm rounded-full border px-md py-tight text-body transition-colors ${CHANNEL_CHIP[channelCheckStatus[channel.channel] ?? 'default']}`}
              >
                <FeedbackMark result={channelFeedback[channel.channel]} />
                {channel.channel}
                {/* The channel's OWN deletions in the last 24h, across every account — the
                    board row's chip counts one (account, channel) pair. A different set, too:
                    this one counts every delivered comment the sweep found gone, including one
                    recorded `failed` mid-send. It is the number that has to explain a back-off,
                    so it lives on the channel and not on the accounts working there. */}
                {(channel.deleted_recent ?? 0) > 0 ? (
                  <span
                    title={t('neurocomment.channels.deletedHint')}
                    className="rounded-full bg-danger-tint px-tight py-px text-tiny font-medium text-danger-deep"
                  >
                    {t('neurocomment.board.deleted', { count: channel.deleted_recent ?? 0 })}
                  </span>
                ) : null}
                <IconButton
                  aria-label={t('neurocomment.channels.remove')}
                  onClick={() => {
                    onRemoveChannel(channel.channel);
                  }}
                  size="sm"
                  shape="circle"
                  tone="danger"
                  className="text-content-subtle"
                >
                  <Icon name="close" size={16} />
                </IconButton>
              </span>
            ))}
            {addingChannel ? (
              <InlineChipEditor
                value={channelInput}
                onChange={onChannelInput}
                onConfirm={onAddChannel}
                onCancel={onCancelAdd}
                placeholder={t('neurocomment.channels.placeholder')}
                inputLabel={t('neurocomment.channels.placeholder')}
                confirmLabel={t('neurocomment.modal.add')}
              />
            ) : (
              <ChipAddButton disabled={campaignId === null} onClick={onStartAdd}>
                {t('neurocomment.channels.addPill')}
              </ChipAddButton>
            )}
          </div>
        </CollapsibleCard>
      </div>
    </CollapsibleCard>
  );
}
