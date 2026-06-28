import { useTranslation } from 'react-i18next';

import type { LogEntry } from '@/shared/api';
import { cn } from '@/shared/lib';

type LogStatus = LogEntry['status'];

const STATUS_CLASS: Record<LogStatus, string> = {
  success: 'bg-success-tint text-success',
  warning: 'bg-warning/10 text-warning',
  error: 'bg-danger-tint text-danger',
};

export function LogStatusBadge({ status }: { status: LogStatus }) {
  const { t } = useTranslation();
  return (
    <span
      className={cn(
        'inline-flex items-center rounded px-2 py-0.5 text-xs font-medium',
        STATUS_CLASS[status],
      )}
    >
      {t(`logs.status.${status}`)}
    </span>
  );
}
