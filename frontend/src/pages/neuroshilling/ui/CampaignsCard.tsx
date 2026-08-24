import { useTranslation } from 'react-i18next';

import type { NeuroshillingCampaign } from '@/shared/api';
import { CollapsibleCard, IconButton } from '@/shared/ui';

// Status tone, in the tokens the neurocomment campaign rows already use — the
// meaning of the status, not a hex, so the two cards cannot drift apart.
const STATUS_TONE = {
  idle: 'text-ink-muted',
  running: 'text-success',
  stopping: 'text-warning-strong',
  done: 'text-primary',
  failed: 'text-danger',
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
      headerClassName="px-lg py-lg"
      bodyClassName="px-lg pb-lg"
      header={<span className="text-lead font-semibold">{t('neuroshilling.campaigns.title')}</span>}
    >
      <div className="flex flex-col gap-sm">
        {campaignList.map((campaign) => {
          const isSelected = campaign.campaign_id === campaignId;
          const status = campaign.status ?? 'idle';
          const tone = STATUS_TONE[status];
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
              className={`cursor-pointer rounded-lg border p-lg ${isSelected ? 'border-primary bg-primary/[0.06]' : 'border-line bg-white'}`}
            >
              <div className="flex justify-between gap-md">
                <div className="min-w-0 flex-1 text-lead font-semibold">{campaign.name}</div>
                <div className="flex shrink-0 items-center gap-md">
                  <span
                    className={`inline-flex items-center gap-tight text-tiny font-medium ${tone}`}
                  >
                    {/* `bg-current` — the dot can never disagree with its label. */}
                    <span className="h-[6px] w-[6px] rounded-full bg-current" />
                    {t(`neuroshilling.campaign.status.${status}`)}
                  </span>
                  <IconButton
                    size="sm"
                    tone="danger"
                    title={t('neuroshilling.campaign.delete')}
                    aria-label={t('neuroshilling.campaign.delete')}
                    onClick={(event) => {
                      event.stopPropagation();
                      onDelete(campaign);
                    }}
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
                  </IconButton>
                </div>
              </div>
            </div>
          );
        })}
        {campaignList.length === 0 ? (
          <div className="py-xl text-center text-body text-ink-subtle">
            {t('neuroshilling.campaigns.none')}
          </div>
        ) : null}
      </div>

      {creating ? (
        // Inline, not a modal: creating asks for a name and nothing else, and the
        // app already spells that shape this way (the channel "add" pill).
        <div className="mt-md flex items-center gap-sm">
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
            className="min-w-0 flex-1 rounded-lg border border-primary bg-white px-md py-sm text-body outline-none"
          />
          <button
            type="button"
            disabled={!createName.trim()}
            onClick={onCreate}
            className="shrink-0 rounded-full bg-primary px-lg py-sm text-tiny font-semibold text-white disabled:opacity-50"
          >
            {t('neuroshilling.campaigns.confirm')}
          </button>
          <button
            type="button"
            aria-label={t('neuroshilling.campaigns.cancel')}
            onClick={onCancelCreate}
            className="shrink-0 rounded-full border border-line-input bg-white px-md py-sm text-body text-ink-muted"
          >
            ×
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={onStartCreate}
          className="mt-md flex w-full items-center justify-center gap-tight rounded-lg border border-dashed border-primary-line bg-white py-md text-body font-medium text-primary hover:border-primary hover:bg-primary-wash"
        >
          {t('neuroshilling.campaigns.create')}
        </button>
      )}
    </CollapsibleCard>
  );
}
