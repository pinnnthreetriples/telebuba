import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ChannelDiscoveryModal } from './ChannelDiscoveryModal';

// Same pill as the sibling "Проверить каналы" button in CampaignsCard, so the two
// read as one control group.
const PILL =
  'shrink-0 rounded-full border border-line bg-surface-card px-md py-xs text-tiny ' +
  'font-medium text-content-muted transition-colors hover:border-action-primary hover:text-action-primary ' +
  'disabled:opacity-50';

type Props = {
  campaignId: string | null;
  campaignName: string;
};

export function ChannelDiscoveryButton({ campaignId, campaignName }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        disabled={campaignId === null}
        onClick={() => {
          setOpen(true);
        }}
        className={PILL}
      >
        {t('neurocomment.modal.discovery.open')}
      </button>
      {open && campaignId !== null ? (
        // Keyed so a campaign switch under the open modal (the page falls back to the
        // first campaign, which changes when one is deleted) remounts it: ticks and
        // adopt state belong to the campaign they were made for, never to the next one.
        <ChannelDiscoveryModal
          key={campaignId}
          campaignId={campaignId}
          campaignName={campaignName}
          onClose={() => {
            setOpen(false);
          }}
        />
      ) : null}
    </>
  );
}
