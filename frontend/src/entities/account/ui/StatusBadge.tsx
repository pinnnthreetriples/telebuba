import { useTranslation } from 'react-i18next';

import { accountHealth, type AccountStatus } from '../model/status';

// The design's status pill: a coloured dot + label, tinted by health.
const HEALTH_CLASS: Record<ReturnType<typeof accountHealth>, string> = {
  ok: 'bg-success-tint text-success',
  warn: 'bg-warning/10 text-warning',
  fail: 'bg-danger-tint text-danger',
};

export function StatusBadge({ status }: { status: AccountStatus }) {
  const { t } = useTranslation();
  return (
    <span
      className={`inline-flex items-center gap-[6px] rounded-full px-[10px] py-[3px] text-[12px] font-medium ${HEALTH_CLASS[accountHealth(status)]}`}
    >
      <span className="h-[6px] w-[6px] rounded-full bg-current" />
      {t(`accounts.status.${status}`)}
    </span>
  );
}
