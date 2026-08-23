import { useTranslation } from 'react-i18next';

import type { NeurocommentChannelRow } from '@/shared/api';

// 'no_data' is a channel with no readiness rows yet (onboarding hasn't produced
// data); the backend now emits it, so it rides in NeurocommentChannelRow['status'].
type ChannelStatus = NeurocommentChannelRow['status'];

// The design's fully-rounded dot-pill. Tone is the token pair the status MEANS —
// text colour plus its tint — and never a per-status hex: four statuses share
// "amber" and four share "red", so a literal per row is four chances to drift.
// The leading dot takes `bg-current`, so it can never disagree with the text.
const STATUS_TONE: Record<ChannelStatus, string> = {
  ready: 'bg-success-tint text-success',
  comments_off: 'bg-track text-ink-muted',
  no_data: 'bg-track text-ink-muted',
  throttled: 'bg-warning-tint text-warning',
  join_by_request: 'bg-warning-tint text-warning',
  join_failed: 'bg-danger-tint text-danger',
  // Amber, not danger: the pair was kicked out and is walking itself back in (one
  // attempt within minutes, then one a day) — nothing for the operator to do yet.
  rejoining: 'bg-warning-tint text-warning',
  // Danger, unlike 'rejoining': this account has no attempt left to spend here and has
  // left the chat. Account rows only — a channel never aggregates to it.
  rejoin_exhausted: 'bg-danger-tint text-danger',
  chat_restricted: 'bg-danger-tint text-danger',
  banned: 'bg-danger-tint text-danger',
  bot_challenge: 'bg-warning-tint text-warning',
  channel_paused: 'bg-warning-tint text-warning',
};

export function ChannelStatusBadge({ status }: { status: ChannelStatus }) {
  const { t } = useTranslation();
  return (
    <span
      className={`inline-flex items-center gap-[5px] rounded-full px-[9px] py-[3px] text-[11px] font-medium ${STATUS_TONE[status]}`}
    >
      <span className="h-[5px] w-[5px] rounded-full bg-current" />
      {t(`neurocomment.channelStatus.${status}`)}
    </span>
  );
}
