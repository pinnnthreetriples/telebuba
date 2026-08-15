import { useTranslation } from 'react-i18next';

import type { NeuroshillingCampaign } from '@/shared/api';
import { CollapsibleCard } from '@/shared/ui';

// Status dot colours, in the palette the neurocomment campaign rows already use.
const STATUS_COLOR = {
  idle: '#74726e',
  running: '#12a150',
  stopping: '#c47d12',
  done: '#0066ff',
  failed: '#c0473f',
} as const;

// The campaigns card: the page's entry point — pick one, make one, delete one.
// Zero hooks besides `useTranslation`, like its neurocomment counterpart: every
// piece of state and every request lives on the page.
export function CampaignsCard({
  campaignList,
  campaignId,
  onSelect,
  onDelete,
  creating,
  createName,
  onStartCreate,
  onCancelCreate,
  onCreateName,
  onCreate,
}: {
  campaignList: NeuroshillingCampaign[];
  campaignId: string | null;
  onSelect: (campaignId: string) => void;
  onDelete: (campaign: NeuroshillingCampaign) => void;
  creating: boolean;
  createName: string;
  onStartCreate: () => void;
  onCancelCreate: () => void;
  onCreateName: (value: string) => void;
  onCreate: () => void;
}) {
  const { t } = useTranslation();
  return (
    <CollapsibleCard
      defaultOpen
      label={t('neuroshilling.campaigns.title')}
      headerClassName="px-4 py-[15px]"
      bodyClassName="px-4 pb-[15px]"
      header={
        <span className="text-[13px] font-semibold">{t('neuroshilling.campaigns.title')}</span>
      }
    >
      <div className="flex flex-col gap-2">
        {campaignList.map((campaign) => {
          const isSelected = campaign.campaign_id === campaignId;
          const status = campaign.status ?? 'idle';
          const color = STATUS_COLOR[status];
          return (
            <div
              key={campaign.campaign_id}
              role="button"
              tabIndex={0}
              onClick={() => {
                onSelect(campaign.campaign_id);
              }}
              // Background in both branches, never in the base: two `bg-*` utilities
              // in one class list are resolved by stylesheet order, so a base
              // `bg-white` would beat the selected tint.
              className={`cursor-pointer rounded-[11px] border p-[13px] ${isSelected ? 'border-primary bg-primary/[0.06]' : 'border-line bg-white'}`}
            >
              <div className="flex justify-between gap-[10px]">
                <div className="min-w-0 flex-1 text-[13px] font-semibold">{campaign.name}</div>
                <div className="flex shrink-0 items-center gap-[10px]">
                  <span
                    className="inline-flex items-center gap-[5px] text-[11px] font-medium"
                    style={{ color }}
                  >
                    <span className="h-[6px] w-[6px] rounded-full" style={{ background: color }} />
                    {t(`neuroshilling.campaign.status.${status}`)}
                  </span>
                  <button
                    type="button"
                    title={t('neuroshilling.campaign.delete')}
                    aria-label={t('neuroshilling.campaign.delete')}
                    onClick={(event) => {
                      event.stopPropagation();
                      onDelete(campaign);
                    }}
                    className="flex h-6 w-6 items-center justify-center rounded-[7px] border border-line bg-white text-ink-subtle transition-colors hover:border-[#f0c9c5] hover:bg-danger-tint hover:text-danger"
                  >
                    <svg
                      width="13"
                      height="13"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.9"
                    >
                      <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                    </svg>
                  </button>
                </div>
              </div>
            </div>
          );
        })}
        {campaignList.length === 0 ? (
          <div className="py-[18px] text-center text-[12px] text-ink-subtle">
            {t('neuroshilling.campaigns.none')}
          </div>
        ) : null}
      </div>

      {creating ? (
        // Inline, not a modal: creating asks for a name and nothing else, and the
        // app already spells that shape this way (the channel "add" pill).
        <div className="mt-[9px] flex items-center gap-2">
          <input
            autoFocus
            value={createName}
            onChange={(event) => {
              onCreateName(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && createName.trim()) onCreate();
              if (event.key === 'Escape') onCancelCreate();
            }}
            placeholder={t('neuroshilling.campaigns.namePlaceholder')}
            aria-label={t('neuroshilling.campaigns.namePlaceholder')}
            className="min-w-0 flex-1 rounded-[10px] border border-primary bg-white px-3 py-[8px] text-[12.5px] outline-none"
          />
          <button
            type="button"
            disabled={!createName.trim()}
            onClick={onCreate}
            className="shrink-0 rounded-full bg-primary px-[14px] py-[8px] text-[12.5px] font-semibold text-white disabled:opacity-50"
          >
            {t('neuroshilling.campaigns.confirm')}
          </button>
          <button
            type="button"
            aria-label={t('neuroshilling.campaigns.cancel')}
            onClick={onCancelCreate}
            className="shrink-0 rounded-full border border-line-input bg-white px-[12px] py-[8px] text-[12.5px] text-ink-muted"
          >
            ×
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={onStartCreate}
          className="mt-[9px] flex w-full items-center justify-center gap-[5px] rounded-[10px] border border-dashed border-[#c7d6f0] bg-white py-[9px] text-[12.5px] font-medium text-primary hover:border-primary hover:bg-[#f2f6ff]"
        >
          {t('neuroshilling.campaigns.create')}
        </button>
      )}
    </CollapsibleCard>
  );
}
