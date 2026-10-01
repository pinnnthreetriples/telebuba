// Renders a backend ISO-8601 UTC timestamp in the browser's own local time
// zone (no explicit `timeZone` → Intl defaults to the runtime's zone).
export function formatLocalTime(iso: string, options: { seconds?: boolean } = {}): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    // 24-hour clock (no AM/PM) — matches the RU UI convention.
    hour12: false,
    ...(options.seconds ? { second: '2-digit' } : {}),
  });
}

// A moment with its date, for anything planned days ahead (a time alone would not
// say WHICH 14:30). The UI language picks the words, the runtime's zone the hour.
export function formatLocalDateTime(ms: number, lang: string): string {
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat(lang, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(date);
}

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

// "через 2 ч" / "5 minutes ago": Intl owns the plural forms, so no key per count.
export function formatRelativeTo(ms: number, now: number, lang: string): string {
  const delta = ms - now;
  const format = new Intl.RelativeTimeFormat(lang, { numeric: 'auto' });
  const size = Math.abs(delta);
  if (size < HOUR_MS) return format.format(Math.round(delta / MINUTE_MS), 'minute');
  if (size < 2 * DAY_MS) return format.format(Math.round(delta / HOUR_MS), 'hour');
  return format.format(Math.round(delta / DAY_MS), 'day');
}
