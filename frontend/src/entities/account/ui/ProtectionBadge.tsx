import { useTranslation } from 'react-i18next';

import type { AccountProtection } from '@/shared/api';
import { Badge } from '@/shared/ui';

import { protectionTone } from '../model/protection';

// «Защита 5/7»: how many of the account's protections are in place. The tooltip lists what
// is missing in plain words; the same text is the chip's accessible name.
export function ProtectionBadge({ protection }: { protection: AccountProtection }) {
  const { t } = useTranslation();
  const chip = t('accounts.protection.chip', {
    score: protection.score,
    total: protection.total,
  });
  const missing = (protection.missing ?? []).map((code) => t(`accounts.protection.${code}`));
  const hint =
    missing.length > 0
      ? `${t('accounts.protection.title')}:\n${missing.map((line) => `• ${line}`).join('\n')}`
      : undefined;
  return (
    <Badge
      tone={protectionTone(protection)}
      size="sm"
      title={hint}
      aria-label={hint ? `${chip}. ${missing.join('; ')}` : chip}
      data-testid="account-protection"
    >
      {chip}
    </Badge>
  );
}
