import { useTranslation } from 'react-i18next';

import type { NeuroshillingCampaign } from '@/shared/api';
import { Button, CollapsibleCard, Icon, IconButton } from '@/shared/ui';

// Status tone, in the tokens the neurocomment campaign rows already use — the
// meaning of the status, not a hex, so the two cards cannot drift apart.
const STATUS_TONE = {
  idle: 'text-content-muted',
  running: 'text-success-deep',
  stopping: 'text-warning-deep',
  done: 'text-action-primary',
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
      header={<span className="type-card-title">{t('neuroshilling.campaigns.title')}</span>}
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
              // `bg-surface-card` would beat the selected tint.
              className={`cursor-pointer rounded-lg border p-lg ${isSelected ? 'border-action-primary bg-info-tint' : 'border-line bg-surface-card'}`}
            >
              <div className="flex justify-between gap-md">
                <div className="min-w-0 flex-1 type-card-title">{campaign.name}</div>
                <div className="flex shrink-0 items-center gap-md">
                  <span
                    className={`inline-flex items-center gap-tight type-caption font-medium ${tone}`}
                  >
                    {/* `bg-current` — the dot can never disagree with its label. */}
                    <span className="size-dot rounded-full bg-current" />
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
                    <Icon name="trash" size={14} />
                  </IconButton>
                </div>
              </div>
            </div>
          );
        })}
        {campaignList.length === 0 ? (
          <div className="py-xl text-center type-prose">{t('neuroshilling.campaigns.none')}</div>
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
            className="min-w-0 flex-1 rounded-lg border border-action-primary bg-surface-card px-md py-sm text-body outline-none"
          />
          <Button variant="primary" size="sm" disabled={!createName.trim()} onClick={onCreate}>
            {t('neuroshilling.campaigns.confirm')}
          </Button>
          <button
            type="button"
            aria-label={t('neuroshilling.campaigns.cancel')}
            onClick={onCancelCreate}
            className="shrink-0 rounded-full border border-line bg-surface-card px-md py-sm text-body text-content-muted"
          >
            ×
          </button>
        </div>
      ) : (
        <Button variant="dashed" size="block" className="mt-md" onClick={onStartCreate}>
          {t('neuroshilling.campaigns.create')}
        </Button>
      )}
    </CollapsibleCard>
  );
}
