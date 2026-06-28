import { useTranslation } from 'react-i18next';

import type { LogEntry } from '@/shared/api';

type LogStatus = LogEntry['status'];

// The design's level pill — tinted by severity.
const STATUS_CLASS: Record<LogStatus, string> = {
  success: 'bg-success-tint text-success',
  warning: 'bg-warning/10 text-warning',
  error: 'bg-danger-tint text-danger',
};

export function LogStatusBadge({ status }: { status: LogStatus }) {
  const { t } = useTranslation();
  return (
    <span
      className={`inline-block rounded-full px-[9px] py-[2px] text-[11px] font-semibold ${STATUS_CLASS[status]}`}
    >
      {t(`logs.status.${status}`)}
    </span>
  );
}
