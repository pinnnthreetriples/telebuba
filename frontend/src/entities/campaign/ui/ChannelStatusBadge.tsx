import { useTranslation } from 'react-i18next';

import type { NeurocommentChannelRow } from '@/shared/api';

// 'no_data' is a channel with no readiness rows yet (onboarding hasn't produced
// data); the backend now emits it, so it rides in NeurocommentChannelRow['status'].
type ChannelStatus = NeurocommentChannelRow['status'];

// The design's fully-rounded dot-pill: per-status text/background hex from the
// status map, with a 5px leading dot tinted to match the text.
const STATUS_COLOR: Record<ChannelStatus, { color: string; bg: string }> = {
  ready: { color: '#12a150', bg: '#ddf7e9' },
  comments_off: { color: '#74726e', bg: '#eeedea' },
  no_data: { color: '#74726e', bg: '#eeedea' },
  throttled: { color: '#9a7b22', bg: '#fbf3e2' },
  join_by_request: { color: '#9a7b22', bg: '#fbf3e2' },
  join_failed: { color: '#c0473f', bg: '#fbecec' },
  // Amber, not danger: the pair was kicked out and is walking itself back in (one
  // attempt within minutes, then one a day) — nothing for the operator to do yet.
  rejoining: { color: '#9a7b22', bg: '#fbf3e2' },
  // Danger, unlike 'rejoining': this account has no attempt left to spend here and has
  // left the chat. Account rows only — a channel never aggregates to it.
  rejoin_exhausted: { color: '#c0473f', bg: '#fbecec' },
  chat_restricted: { color: '#c0473f', bg: '#fbecec' },
  banned: { color: '#c0473f', bg: '#fbecec' },
  bot_challenge: { color: '#9a7b22', bg: '#fbf3e2' },
  channel_paused: { color: '#9a7b22', bg: '#fbf3e2' },
};

export function ChannelStatusBadge({ status }: { status: ChannelStatus }) {
  const { t } = useTranslation();
  const { color, bg } = STATUS_COLOR[status];
  return (
    <span
      className="inline-flex items-center gap-[5px] rounded-full px-[9px] py-[3px] text-[11.5px] font-medium"
      style={{ color, background: bg }}
    >
      <span className="h-[5px] w-[5px] rounded-full" style={{ background: color }} />
      {t(`neurocomment.channelStatus.${status}`)}
    </span>
  );
}
