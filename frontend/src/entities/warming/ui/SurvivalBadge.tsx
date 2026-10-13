import { useTranslation } from 'react-i18next';

import type { AccountSurvival } from '@/shared/api';
import { Badge, type BadgeTone } from '@/shared/ui';

// «День 12 после выпуска · жив» on a released account's card. The backend decides the
// status from the lifecycle journal; which tone it deserves is warming knowledge — a
// wound is a warning, an account with no clean answer since release is not yet a
// failure — so it lives in the warming entity, where every card can reach it.
const TONE: Record<AccountSurvival['status'], BadgeTone> = {
  warming: 'info',
  alive: 'success',
  wounded: 'warning',
  unchecked: 'neutral',
  dead: 'danger',
  censored: 'neutral',
};

export function SurvivalBadge({ survival }: { survival: AccountSurvival }) {
  const { t } = useTranslation();
  return (
    <Badge tone={TONE[survival.status]} dot>
      {t('warming.survival.badge', {
        days: survival.days_since_release,
        status: t(`warming.survival.status.${survival.status}`),
      })}
    </Badge>
  );
}
