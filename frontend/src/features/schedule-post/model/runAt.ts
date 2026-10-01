// Publish-time arithmetic for scheduled photos and stories. Pure and in epoch
// milliseconds throughout: a wall-clock string never takes part in the maths, so a
// daylight-saving jump cannot shift a spread, and `now` / `rng` are parameters so
// the tests do not depend on the runner's clock or zone.

const MINUTE_MS = 60_000;

// The server's own window (SCHEDULED_POSTS__MIN_LEAD_SECONDS / MAX_LEAD_DAYS).
// Mirrored so the field says "too soon" before an upload is spent on a refusal;
// the server stays the authority and answers `scheduled_run_at_out_of_range`.
export const MIN_LEAD_MS = MINUTE_MS;
export const MAX_LEAD_MS = 30 * 24 * 60 * MINUTE_MS;
// A bulk run takes a while to walk its accounts; its base starts further out so
// the last row still lands inside the window when it is finally sent.
export const BULK_MIN_LEAD_MS = 5 * MINUTE_MS;
// Headroom a row gets when it is actually sent: the request needs a moment.
const SEND_HEADROOM_MS = 90_000;
const ROUND_TO_MS = 5 * MINUTE_MS;

export type RunAtProblem = 'empty' | 'past' | 'tooFar';

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

/** `<input type="datetime-local">` value for a moment, in the browser's zone. */
export function toLocalInput(ms: number): string {
  const date = new Date(ms);
  return (
    `${String(date.getFullYear())}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/** The moment a `datetime-local` value names (a zoneless value is local time). */
export function fromLocalInput(value: string): number | null {
  if (!value) return null;
  const ms = new Date(value).getTime();
  return Number.isNaN(ms) ? null : ms;
}

/** A tidy first suggestion: `leadMs` from now, rounded up to five minutes. */
export function defaultRunAt(now: number, leadMs = 15 * MINUTE_MS): number {
  return Math.ceil((now + leadMs) / ROUND_TO_MS) * ROUND_TO_MS;
}

export function runAtProblem(
  ms: number | null,
  now: number,
  minLeadMs = MIN_LEAD_MS,
): RunAtProblem | null {
  if (ms === null) return 'empty';
  if (ms < now + minLeadMs) return 'past';
  if (ms > now + MAX_LEAD_MS) return 'tooFar';
  return null;
}

/** `count` moments starting at `start`, one every `stepMinutes`. */
export function spreadEvery(start: number, count: number, stepMinutes: number): number[] {
  return Array.from({ length: count }, (_, index) => start + index * stepMinutes * MINUTE_MS);
}

/**
 * One moment per account of a bulk run: `spreadMinutes` apart, each nudged by a
 * random share of half a gap so the accounts do not post on a visible grid. The
 * nudge stays under the gap, so the order of the accounts is kept.
 */
export function bulkRunAts(
  base: number,
  count: number,
  spreadMinutes: number,
  rng: () => number,
): number[] {
  const gap = spreadMinutes * MINUTE_MS;
  return Array.from(
    { length: count },
    (_, index) => base + index * gap + Math.floor(rng() * (gap / 2)),
  );
}

/** The latest moment `bulkRunAts` can hand out: the last account plus its full nudge. */
export function bulkTailMs(base: number, count: number, spreadMinutes: number): number {
  const gap = spreadMinutes * MINUTE_MS;
  return base + Math.max(0, count - 1) * gap + gap / 2;
}

/** Push a planned moment that time has caught up with back into the window. */
export function clampToLead(ms: number, now: number): number {
  return Math.max(ms, now + SEND_HEADROOM_MS);
}

/** A key the server uses to recognise a retried row of one batch. */
export function newBatchId(): string {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

export function toIso(ms: number): string {
  return new Date(ms).toISOString();
}
