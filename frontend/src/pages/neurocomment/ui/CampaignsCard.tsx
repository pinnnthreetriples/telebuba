import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import type { NeurocommentCampaign } from '@/shared/api';
import { type FeedbackResult } from '@/shared/lib';
import { Button, CollapsibleCard, FeedbackMark, Icon, IconButton, SurfHover } from '@/shared/ui';

// Tone is the token the status MEANS (running = success, held = amber, shelved =
// muted), so the pill can't drift from the rest of the design system.
const STATUS_TONE = {
  active: 'text-success-deep',
  paused: 'text-warning-deep',
  archived: 'text-ink-muted',
} as const;

// Channel-chip tone driven by the live "Проверить каналы" verdict: banned = red
// (persists), ok = green (5s flash), default = the neutral gray pill.
const CHANNEL_CHIP = {
  banned: 'border-danger bg-danger-tint text-danger-deep',
  ok: 'border-success bg-success-tint text-success-deep',
  default: 'border-line bg-canvas text-ink-body',
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
      header={<span className="text-lead font-semibold">{t('neurocomment.campaigns.title')}</span>}
    >
      <div className="flex flex-col gap-sm">
        {campaignList.map((campaign) => {
          const isSelected = campaign.campaign_id === campaignId;
          // Per-campaign run state comes from the campaign's own status,
          // not the global engine (finding #2).
          const isRunning = campaign.status === 'active';
          const tone = STATUS_TONE[campaign.status];
          return (
            <SurfHover
              key={campaign.campaign_id}
              shift={144}
              surfaceId={`camp-surf-${campaign.campaign_id}`}
              open={openCampaignActions === campaign.campaign_id}
              actions={
                <>
                  <button
                    type="button"
                    title={
                      isRunning ? t('neurocomment.campaign.pause') : t('neurocomment.campaign.run')
                    }
                    onClick={() => {
                      onToggleStatus(campaign);
                    }}
                    className={`flex w-action items-center justify-center border-none bg-transparent ${isRunning ? 'text-warning-deep' : 'text-success-deep'}`}
                  >
                    {isRunning ? <Icon name="pause" size={18} /> : <Icon name="play" size={18} />}
                  </button>
                  <button
                    type="button"
                    title={t('neurocomment.campaign.editPrompt')}
                    onClick={() => {
                      // Selecting the campaign too keeps the board query (and thus the
                      // prompt modal's account list) on THIS campaign (finding #5).
                      onEditPrompt(campaign);
                    }}
                    className="flex w-action items-center justify-center border-none bg-transparent text-primary"
                  >
                    <Icon name="pencil" size={18} />
                  </button>
                  <button
                    type="button"
                    title={t('neurocomment.campaign.delete')}
                    onClick={() => {
                      onDelete(campaign);
                    }}
                    className="flex w-action items-center justify-center border-none bg-transparent text-danger"
                  >
                    <Icon name="trash" size={18} />
                  </button>
                </>
              }
              surface={
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => {
                    onSelect(campaign.campaign_id);
                  }}
                  // Background lives in both branches, never in the base: two `bg-*`
                  // utilities in one class list are resolved by stylesheet order, and
                  // `bg-white` wins over the selected tint.
                  className={`cursor-pointer rounded-lg border p-lg ${isSelected ? 'border-primary bg-primary/[0.06]' : 'border-line bg-white'}`}
                >
                  <div className="flex justify-between gap-md">
                    <div className="min-w-0 flex-1">
                      <div className="mb-tight text-lead font-semibold">{campaign.name}</div>
                      <div className="text-tiny text-ink-muted">
                        {t('neurocomment.campaign.meta', {
                          channels: campaign.channel_count ?? 0,
                          accounts: campaign.account_count ?? 0,
                        })}
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-sm">
                      <span
                        className={`inline-flex items-center gap-tight text-tiny font-medium ${tone}`}
                      >
                        {/* `bg-current` — the dot can never disagree with its label. */}
                        <span className="size-dot rounded-full bg-current" />
                        {t(`neurocomment.campaign.status.${campaign.status}`)}
                      </span>
                      <IconButton
                        size="sm"
                        tone="primary"
                        title={t('neurocomment.campaign.actions')}
                        aria-label={t('neurocomment.campaign.actions')}
                        aria-expanded={openCampaignActions === campaign.campaign_id}
                        onClick={(event) => {
                          event.stopPropagation();
                          onToggleActions(campaign.campaign_id);
                        }}
                      >
                        <Icon name="gear" size={14} />
                      </IconButton>
                    </div>
                  </div>
                </div>
              }
            />
          );
        })}
        {campaignList.length === 0 ? (
          <div className="py-xl text-center text-body text-ink-subtle">
            {t('neurocomment.campaigns.none')}
          </div>
        ) : null}
      </div>

      <Button variant="dashed" size="block" className="mt-md" onClick={onCreate}>
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
          header={
            <span className="text-body font-semibold">{t('neurocomment.channels.title')}</span>
          }
        >
          <div className="mb-md flex items-center justify-between gap-sm">
            <span className="min-w-0 truncate text-tiny font-medium text-primary">
              {activeCampaign?.name ?? ''}
            </span>
            <div className="flex shrink-0 items-center gap-sm">
              {discoverySlot}
              <button
                type="button"
                disabled={campaignId === null || checkingChannels}
                onClick={onCheckChannels}
                className="shrink-0 rounded-full border border-line bg-white px-md py-xs text-tiny font-medium text-ink-muted transition-colors hover:border-primary hover:text-primary disabled:opacity-50"
              >
                {checkingChannels
                  ? t('neurocomment.channels.checking')
                  : t('neurocomment.channels.check')}
              </button>
            </div>
          </div>
          <div className="flex flex-wrap gap-sm">
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
                    className="rounded-full bg-danger-tint px-tight py-px text-micro font-medium text-danger-deep"
                  >
                    {t('neurocomment.board.deleted', { count: channel.deleted_recent ?? 0 })}
                  </span>
                ) : null}
                <button
                  type="button"
                  aria-label={t('neurocomment.channels.remove')}
                  onClick={() => {
                    onRemoveChannel(channel.channel);
                  }}
                  className="text-lead leading-none text-ink-subtle"
                >
                  ×
                </button>
              </span>
            ))}
            {addingChannel ? (
              <span className="inline-flex items-center gap-tight rounded-full border border-primary bg-white py-xs pl-md pr-xs">
                <input
                  autoFocus
                  value={channelInput}
                  onChange={(event) => {
                    onChannelInput(event.target.value);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') onAddChannel();
                    if (event.key === 'Escape') onCancelAdd();
                  }}
                  placeholder={t('neurocomment.channels.placeholder')}
                  aria-label={t('neurocomment.channels.placeholder')}
                  className="w-col border-none bg-transparent text-body outline-none"
                />
                <button
                  type="button"
                  aria-label={t('neurocomment.modal.add')}
                  disabled={!channelInput.trim()}
                  onClick={onAddChannel}
                  className="flex size-chip shrink-0 items-center justify-center rounded-full bg-primary text-white disabled:opacity-50"
                >
                  <Icon name="check" size={12} />
                </button>
              </span>
            ) : (
              <button
                type="button"
                // Not `Button variant="dashed"`, and deliberately: this is the muted
                // inline adder that stands in a row of channel chips, drawn in
                // `line-strong` and `ink-muted` where the block adder under a list is
                // drawn in blue. It shares only the dash. Its twin is the warming
                // page's; if a third appears, that is the moment it earns a rung.
                disabled={campaignId === null}
                onClick={onStartAdd}
                className="inline-flex items-center gap-tight rounded-full border border-dashed border-line-strong bg-white px-md py-tight text-body text-ink-muted hover:border-primary hover:text-primary disabled:opacity-50"
              >
                {t('neurocomment.channels.addPill')}
              </button>
            )}
          </div>
        </CollapsibleCard>
      </div>
    </CollapsibleCard>
  );
}
