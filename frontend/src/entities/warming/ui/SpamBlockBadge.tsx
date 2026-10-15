import { useTranslation } from 'react-i18next';

import { Badge } from '@/shared/ui';

// «Спамблок»: @SpamBot confirmed this account's spam block (it cannot write to strangers).
// Permanent for the account's life, so every card that shows the account wears it —
// warming, ready and warmed alike — once the backend sends `spam_blocked_at`.
export function SpamBlockBadge({ blockedAt }: { blockedAt?: string | null }) {
  const { t } = useTranslation();
  if (!blockedAt) return null;
  return (
    <Badge tone="danger" dot title={t('warming.spamBlock.badgeHint')}>
      {t('warming.spamBlock.badge')}
    </Badge>
  );
}
