export const MINUTE_MS = 60_000;

// "1:20" — hours and minutes until `iso`; `null` once it has passed or is unreadable.
export function countdown(iso: string | null | undefined, now: number): string | null {
  if (!iso) return null;
  const target = new Date(iso).getTime();
  if (Number.isNaN(target) || target <= now) return null;
  const minutes = Math.ceil((target - now) / MINUTE_MS);
  return `${String(Math.floor(minutes / 60))}:${String(minutes % 60).padStart(2, '0')}`;
}
