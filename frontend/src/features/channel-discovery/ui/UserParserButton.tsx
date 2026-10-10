import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/shared/ui';

import { UserParserModal } from './UserParserModal';

// Третья кнопка той же группы, что «Найти каналы» и «Проверить каналы», и того же вида.
const COMPACT =
  'text-small text-content-muted hover:border-action-primary hover:text-action-primary';

type Props = {
  campaignId: string | null;
  campaignName: string;
  campaignChannels: readonly string[];
};

export function UserParserButton({ campaignId, campaignName, campaignChannels }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button
        size="sm"
        disabled={campaignId === null}
        onClick={() => {
          setOpen(true);
        }}
        className={COMPACT}
      >
        {t('userParser.open')}
      </Button>
      {open && campaignId !== null ? (
        <UserParserModal
          key={campaignId}
          campaignName={campaignName}
          campaignChannels={campaignChannels}
          onClose={() => {
            setOpen(false);
          }}
        />
      ) : null}
    </>
  );
}
