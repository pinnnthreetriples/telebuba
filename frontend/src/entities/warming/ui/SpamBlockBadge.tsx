import { useTranslation } from 'react-i18next';

import { Badge } from '@/shared/ui';

// «Спамблок»: @SpamBot confirmed this account's spam block (it cannot write to strangers).
// Every card that shows the account wears it — warming, ready and warmed alike — while
// the backend sends `spam_blocked_at`; a later clean @SpamBot answer clears it.
export function SpamBlockBadge({ blockedAt }: { blockedAt?: string | null }) {
  const { t } = useTranslation();
  if (!blockedAt) return null;
  return (
    <Badge tone="danger" dot title={t('warming.spamBlock.badgeHint')}>
      {t('warming.spamBlock.badge')}
    </Badge>
  );
}
