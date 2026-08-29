import { useTranslation } from 'react-i18next';

import { Badge, type BadgeTone } from '@/shared/ui';

import { accountDesignStatus, type AccountStatus } from '../model/status';

// The design's statusMap, read as `Badge` tones. It stays in the account entity
// because which tone a status deserves is account knowledge — `spam` being a
// warning and not a failure is a decision about accounts, not about pills.
const STATUS_TONE: Record<ReturnType<typeof accountDesignStatus>, BadgeTone> = {
  active: 'success',
  spam: 'warning',
  code: 'info',
  banned: 'danger',
};

export function StatusBadge({ status }: { status: AccountStatus }) {
  const { t } = useTranslation();
  return (
    <Badge tone={STATUS_TONE[accountDesignStatus(status)]} size="sm" dot>
      {t(`accounts.status.${status}`)}
    </Badge>
  );
}
