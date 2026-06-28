import { useTranslation } from 'react-i18next';

import type { NeurocommentChannelRow } from '@/shared/api';
import { cn } from '@/shared/lib';

type ChannelStatus = NeurocommentChannelRow['status'];

const STATUS_CLASS: Record<ChannelStatus, string> = {
  ready: 'bg-success-tint text-success',
  comments_off: 'bg-line text-ink-muted',
  throttled: 'bg-line text-ink-muted',
  join_by_request: 'bg-warning/10 text-warning',
  chat_restricted: 'bg-danger-tint text-danger',
  bot_challenge: 'bg-warning/10 text-warning',
  bot_challenge_backoff: 'bg-warning/10 text-warning',
};

export function ChannelStatusBadge({ status }: { status: ChannelStatus }) {
  const { t } = useTranslation();
  return (
    <span
      className={cn(
        'inline-flex items-center rounded px-2 py-0.5 text-xs font-medium',
        STATUS_CLASS[status],
      )}
    >
      {t(`neurocomment.channelStatus.${status}`)}
    </span>
  );
}
