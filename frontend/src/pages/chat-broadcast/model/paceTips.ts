// The pictures and the advice beside the pause settings, worked out from the draft. One
// account, one message per chat, the pause between chats as its only clock: a deliberately
// plain model, because it is what the operator reads at a glance, not a forecast.
//
// The recommendations are the agreed defaults where there is one (an hour after a join, one
// to two hours between rounds) and, for the pause between chats, the pause that spreads the
// hourly limit over the whole hour: faster sends the limit in a burst and then sleeps.

export type Range = { min: number; max: number };
export type HourVerdict = 'burst' | 'even' | 'slow' | 'free';
export type HourPlan = {
  sent: number;
  // When each send goes out, in seconds from the start of the hour.
  sends: number[];
  busyMinutes: number;
  restMinutes: number;
  verdict: HourVerdict;
};

const HOUR = 3600;
// A sleep shorter than this is the pause's own randomness, not a burst.
const BURST_REST = 300;
// The settings dialog caps a pause at an hour.
const MAX_PAUSE = 3600;

export const EVEN_JOIN_DELAY = 60;
export const EVEN_REST: Range = { min: 60, max: 120 };

export function hourPlan(betweenChats: Range, perHour: number | null): HourPlan {
  const pause = Math.max(1, (betweenChats.min + betweenChats.max) / 2);
  const fits = Math.ceil(HOUR / pause);
  const sent = perHour === null ? fits : Math.min(perHour, fits);
  // At the limit the next send waits for the first one to leave the hour.
  const rest = perHour !== null && sent === perHour ? Math.max(0, HOUR - sent * pause) : 0;
  const verdict: HourVerdict =
    perHour === null ? 'free' : rest >= BURST_REST ? 'burst' : sent < perHour ? 'slow' : 'even';
  return {
    sent,
    sends: Array.from({ length: sent }, (_unused, index) => Math.round(index * pause)),
    busyMinutes: Math.round((HOUR - rest) / 60),
    restMinutes: Math.round(rest / 60),
    verdict,
  };
}

// A range around the pause that lets out exactly `perHour` in an hour.
export function evenPause(perHour: number): Range {
  const base = HOUR / perHour;
  const max = Math.min(MAX_PAUSE, Math.round(base + base / 6));
  return { min: Math.max(0, Math.round(2 * base - max)), max };
}

export function joinVerdict(minutes: number): 'risky' | 'short' | 'good' {
  if (minutes === 0) return 'risky';
  return minutes < EVEN_JOIN_DELAY ? 'short' : 'good';
}

// The shortest rest is the one that decides how often a chat sees the same text.
export function restVerdict(minutes: Range): 'often' | 'good' {
  return minutes.min < EVEN_REST.min ? 'often' : 'good';
}
