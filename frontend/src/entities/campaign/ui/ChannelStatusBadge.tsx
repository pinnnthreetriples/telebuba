import { useTranslation } from 'react-i18next';

import type { NeurocommentChannelRow } from '@/shared/api';
import { Badge, type BadgeTone } from '@/shared/ui';

// 'no_data' is a channel with no readiness rows yet (onboarding hasn't produced
// data); the backend now emits it, so it rides in NeurocommentChannelRow['status'].
type ChannelStatus = NeurocommentChannelRow['status'];

// Tone is the token pair the status MEANS, and never a per-status hex: four statuses
// share "amber" and four share "red", so a literal per row is four chances to drift.
// The map stays here rather than in `shared/` because twelve channel states are
// neurocomment's knowledge, and a pill has no opinion about any of them.
const STATUS_TONE: Record<ChannelStatus, BadgeTone> = {
  ready: 'success',
  comments_off: 'neutral',
  no_data: 'neutral',
  throttled: 'warning',
  join_by_request: 'warning',
  join_failed: 'danger',
  // Amber, not danger: the pair was kicked out and is walking itself back in (one
  // attempt within minutes, then one a day) — nothing for the operator to do yet.
  rejoining: 'warning',
  // Danger, unlike 'rejoining': this account has no attempt left to spend here and has
  // left the chat. Account rows only — a channel never aggregates to it.
  rejoin_exhausted: 'danger',
  chat_restricted: 'danger',
  banned: 'danger',
  bot_challenge: 'warning',
  channel_paused: 'warning',
};

export function ChannelStatusBadge({ status }: { status: ChannelStatus }) {
  const { t } = useTranslation();
  return (
    <Badge tone={STATUS_TONE[status]} size="sm" dot>
      {t(`neurocomment.channelStatus.${status}`)}
    </Badge>
  );
}
