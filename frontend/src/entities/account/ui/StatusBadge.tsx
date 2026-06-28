import { useTranslation } from 'react-i18next';

import { cn } from '@/shared/lib';

import { accountHealth, type AccountStatus } from '../model/status';

const HEALTH_CLASS: Record<ReturnType<typeof accountHealth>, string> = {
  ok: 'bg-success-tint text-success',
  warn: 'bg-warning/10 text-warning',
  fail: 'bg-danger-tint text-danger',
};

export function StatusBadge({ status }: { status: AccountStatus }) {
  const { t } = useTranslation();
  return (
    <span
      className={cn(
        'inline-flex items-center rounded px-2 py-0.5 text-xs font-medium',
        HEALTH_CLASS[accountHealth(status)],
      )}
    >
      {t(`accounts.status.${status}`)}
    </span>
  );
}
