import { useTranslation } from 'react-i18next';

import type { LogEntry } from '@/shared/api';

type LogStatus = LogEntry['status'];

// The design's level pill — solid severity tint.
const STATUS_CLASS: Record<LogStatus, string> = {
  success: 'bg-success-tint text-success-deep',
  warning: 'bg-warning-tint text-warning-deep',
  error: 'bg-danger-tint text-danger-deep',
};

export function LogStatusBadge({ status }: { status: LogStatus }) {
  const { t } = useTranslation();
  return (
    <span
      className={`inline-block rounded-full px-md py-xs text-tiny font-semibold ${STATUS_CLASS[status]}`}
    >
      {t(`logs.status.${status}`)}
    </span>
  );
}
