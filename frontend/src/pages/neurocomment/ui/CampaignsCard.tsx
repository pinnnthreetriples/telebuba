import { useTranslation } from 'react-i18next';

import type { NeurocommentCampaign } from '@/shared/api';
import { type FeedbackResult } from '@/shared/lib';
import { CollapsibleCard, FeedbackMark } from '@/shared/ui';

import { SurfHover } from './SurfHover';

const STATUS_COLOR = {
  active: '#12a150',
  paused: '#c47d12',
  archived: '#74726e',
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
}: {
  campaignList: NeurocommentCampaign[];
  campaignId: string | null;
  activeCampaign: NeurocommentCampaign | null;
  boardChannels: { channel: string }[];
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
}) {
  const { t } = useTranslation();
  return (
    <CollapsibleCard
      defaultOpen
      label={t('neurocomment.campaigns.title')}
      headerClassName="px-4 py-[15px]"
      bodyClassName="px-4 pb-[15px]"
      header={
        <span className="text-[13px] font-semibold">{t('neurocomment.campaigns.title')}</span>
      }
    >
      <div className="flex flex-col gap-2">
        {campaignList.map((campaign) => {
          const isSelected = campaign.campaign_id === campaignId;
          // Per-campaign run state comes from the campaign's own status,
          // not the global engine (finding #2).
          const isRunning = campaign.status === 'active';
          const color = STATUS_COLOR[campaign.status];
          return (
            <SurfHover
              key={campaign.campaign_id}
              shift={156}
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
                    className={`flex w-[52px] items-center justify-center border-none bg-transparent ${isRunning ? 'text-[#c47d12]' : 'text-success'}`}
                  >
                    {isRunning ? (
                      <svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor">
                        <rect x="6" y="5" width="4" height="14" rx="1" />
                        <rect x="14" y="5" width="4" height="14" rx="1" />
                      </svg>
                    ) : (
                      <svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M7 5.5v13a1 1 0 0 0 1.5.87l11-6.5a1 1 0 0 0 0-1.74l-11-6.5A1 1 0 0 0 7 5.5z" />
                      </svg>
                    )}
                  </button>
                  <button
                    type="button"
                    title={t('neurocomment.campaign.editPrompt')}
                    onClick={() => {
                      // Selecting the campaign too keeps the board query (and thus the
                      // prompt modal's account list) on THIS campaign (finding #5).
                      onEditPrompt(campaign);
                    }}
                    className="flex w-[52px] items-center justify-center border-none bg-transparent text-primary"
                  >
                    <svg
                      width="17"
                      height="17"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.8"
                    >
                      <path d="M12 20h9" />
                      <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
                    </svg>
                  </button>
                  <button
                    type="button"
                    title={t('neurocomment.campaign.delete')}
                    onClick={() => {
                      onDelete(campaign);
                    }}
                    className="flex w-[52px] items-center justify-center border-none bg-transparent text-danger"
                  >
                    <svg
                      width="17"
                      height="17"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.8"
                    >
                      <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                    </svg>
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
                  className={`cursor-pointer rounded-[11px] border bg-white p-[13px] ${isSelected ? 'border-primary bg-primary/[0.06]' : 'border-line'}`}
                >
                  <div className="flex justify-between gap-[10px]">
                    <div className="min-w-0 flex-1">
                      <div className="mb-[5px] text-[13px] font-semibold">{campaign.name}</div>
                      <div className="text-[11px] text-ink-muted">
                        {t('neurocomment.campaign.meta', {
                          channels: campaign.channel_count ?? 0,
                          accounts: campaign.account_count ?? 0,
                        })}
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-2">
                      <span
                        className="inline-flex items-center gap-[5px] text-[11px] font-medium"
                        style={{ color }}
                      >
                        <span
                          className="h-[6px] w-[6px] rounded-full"
                          style={{ background: color }}
                        />
                        {t(`neurocomment.campaign.status.${campaign.status}`)}
                      </span>
                      <button
                        type="button"
                        title={t('neurocomment.campaign.actions')}
                        aria-label={t('neurocomment.campaign.actions')}
                        aria-expanded={openCampaignActions === campaign.campaign_id}
                        onClick={(event) => {
                          event.stopPropagation();
                          onToggleActions(campaign.campaign_id);
                        }}
                        className="flex h-6 w-6 items-center justify-center rounded-[7px] border border-line bg-white text-ink-subtle transition-colors hover:border-[#cbd7ec] hover:bg-[#f2f6ff] hover:text-primary"
                      >
                        <svg
                          width="13"
                          height="13"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                        >
                          <circle cx="12" cy="12" r="3" />
                          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
                        </svg>
                      </button>
                    </div>
                  </div>
                </div>
              }
            />
          );
        })}
        {campaignList.length === 0 ? (
          <div className="py-[18px] text-center text-[12px] text-ink-subtle">
            {t('neurocomment.campaigns.none')}
          </div>
        ) : null}
      </div>

      <button
        type="button"
        onClick={onCreate}
        className="mt-[9px] flex w-full items-center justify-center gap-[5px] rounded-[10px] border border-dashed border-[#c7d6f0] bg-white py-[9px] text-[12.5px] font-medium text-primary hover:border-primary hover:bg-[#f2f6ff]"
      >
        {t('neurocomment.campaigns.create')}
      </button>

      {/* campaign channels */}
      <div className="mt-[13px] border-t border-[#f0eeeb] pt-3">
        <CollapsibleCard
          defaultOpen
          wrapperClassName=""
          headerClassName="px-0 py-0"
          bodyClassName="px-0 pb-0 pt-[11px]"
          label={t('neurocomment.channels.title')}
          header={
            <span className="text-[12.5px] font-semibold">{t('neurocomment.channels.title')}</span>
          }
        >
          {activeCampaign ? (
            <div className="mb-[10px] truncate text-[11.5px] font-medium text-primary">
              {activeCampaign.name}
            </div>
          ) : null}
          <div className="flex flex-wrap gap-[7px]">
            {boardChannels.map((channel) => (
              <span
                key={channel.channel}
                className="inline-flex items-center gap-[6px] rounded-full border border-line bg-[#f4f3f0] px-[11px] py-[5px] text-[12px] text-[#3a3a3a]"
              >
                <FeedbackMark result={channelFeedback[channel.channel]} />
                {channel.channel}
                <button
                  type="button"
                  aria-label={t('neurocomment.channels.remove')}
                  onClick={() => {
                    onRemoveChannel(channel.channel);
                  }}
                  className="text-[14px] leading-none text-[#b5b3ae]"
                >
                  ×
                </button>
              </span>
            ))}
            {addingChannel ? (
              <span className="inline-flex items-center gap-1 rounded-full border border-primary bg-white py-[3px] pl-[11px] pr-1">
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
                  className="w-[150px] border-none bg-transparent text-[12px] outline-none"
                />
                <button
                  type="button"
                  aria-label={t('neurocomment.modal.add')}
                  disabled={!channelInput.trim()}
                  onClick={onAddChannel}
                  className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-primary text-white disabled:opacity-50"
                >
                  <svg
                    width="12"
                    height="12"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#fff"
                    strokeWidth="3"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M20 6 9 17l-5-5" />
                  </svg>
                </button>
              </span>
            ) : (
              <button
                type="button"
                disabled={campaignId === null}
                onClick={onStartAdd}
                className="inline-flex items-center gap-1 rounded-full border border-dashed border-line-strong bg-white px-[11px] py-[5px] text-[12px] text-ink-muted hover:border-primary hover:text-primary disabled:opacity-50"
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
