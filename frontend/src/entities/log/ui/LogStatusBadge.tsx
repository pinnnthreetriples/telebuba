import { useTranslation } from 'react-i18next';

import type { LogEntry } from '@/shared/api';
import { Badge, type BadgeTone } from '@/shared/ui';

type LogStatus = LogEntry['status'];

// The design's level pill. No dot: a log row is already a list of severities read
// down a column, and a dot on every one of them is a column of dots.
const STATUS_TONE: Record<LogStatus, BadgeTone> = {
  success: 'success',
  warning: 'warning',
  error: 'danger',
};

export function LogStatusBadge({ status }: { status: LogStatus }) {
  const { t } = useTranslation();
  return (
    <Badge tone={STATUS_TONE[status]} size="sm">
      {t(`logs.status.${status}`)}
    </Badge>
  );
}
