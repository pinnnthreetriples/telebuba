import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/shared/ui';

import { ChannelDiscoveryModal } from './ChannelDiscoveryModal';

// Та же кнопка, что соседняя «Проверить каналы»: они читаются одной группой контролов, и
// теперь это буквально один компонент, а не одинаково набранная строка. Строка была
// `const PILL` — и именно поэтому её не видел гейт, читавший атрибуты элемента: класс
// приходил идентификатором. Гейт с тех пор смотрит и в константы файла.
//
// `text-tiny` — решение места вызова, и оно не про форму: обе кнопки стоят в узкой колонке
// рядом с именем кампании, которое и есть подлежащее строки. На рунге контрола (`body`)
// пара занимает её целиком, и от имени остаётся «К…».
const COMPACT =
  'text-tiny text-content-muted hover:border-action-primary hover:text-action-primary';

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
        className={COMPACT}
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
