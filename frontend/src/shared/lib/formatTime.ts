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
