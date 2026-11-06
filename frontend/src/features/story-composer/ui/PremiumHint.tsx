import { useTranslation } from 'react-i18next';

import { Notice } from '@/shared/ui';

import { CAPTION_MAX_FREE } from '../model/limits';
import type { PremiumFeature } from '../model/types';

// What an account without Premium will not get. Silent when nothing would be lost, so
// it never nags about a plain story. `nonPremiumCount` is the bulk form: how many of the
// selected accounts lack Premium (0: none, show nothing).
export function PremiumHint({
  premium,
  loss,
  nonPremiumCount,
  freeCaptionMax = CAPTION_MAX_FREE,
}: {
  premium: boolean | null;
  loss: readonly PremiumFeature[];
  nonPremiumCount?: number;
  freeCaptionMax?: number;
}) {
  const { t } = useTranslation();
  if (loss.length === 0) return null;
  const list = loss
    .map((feature) => t(`storyComposer.hint.loss.${feature}`, { free: freeCaptionMax }))
    .join(', ');

  if (nonPremiumCount !== undefined) {
    if (nonPremiumCount <= 0) return null;
    return (
      <Notice tone="warning" role="status">
        {t('storyComposer.hint.bulk', { count: nonPremiumCount, list })}
      </Notice>
    );
  }
  if (premium === true) return null;
  if (premium === false) {
    return (
      <Notice tone="warning" role="status">
        {t('storyComposer.hint.free', { list })}
      </Notice>
    );
  }
  return (
    <Notice tone="info" role="status">
      {t('storyComposer.hint.unknown', { list })}
    </Notice>
  );
}
