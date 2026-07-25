import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ChannelDiscoveryModal } from './ChannelDiscoveryModal';

// Same pill as the sibling "Проверить каналы" button in CampaignsCard, so the two
// read as one control group.
const PILL =
  'shrink-0 rounded-full border border-line-input bg-white px-[11px] py-[4px] text-[11.5px] ' +
  'font-medium text-ink-muted transition-colors hover:border-primary hover:text-primary ' +
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
        <ChannelDiscoveryModal
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
