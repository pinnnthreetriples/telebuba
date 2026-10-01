import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/shared/ui';

import { ChannelDiscoveryModal } from './ChannelDiscoveryModal';

type Props = {
  campaignId: string | null;
  campaignName: string;
};

export function ChannelDiscoveryButton({ campaignId, campaignName }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button
        size="xs"
        disabled={campaignId === null}
        onClick={() => {
          setOpen(true);
        }}
        variant="compactChannel"
        textSize="tiny"
      >
        {t('neurocomment.modal.discovery.open')}
      </Button>
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
