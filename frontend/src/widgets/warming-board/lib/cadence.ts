import type { CadencePreview } from '@/shared/api';

type ActivityPersona = CadencePreview['persona'];

// «5–6» or «20» — a visits range as the persona hint shows it.
function span(min: number, max: number): string {
  return min === max ? String(min) : `${String(min)}–${String(max)}`;
}

// The persona's real visits a day in the first phase and the last, from the board's
// `cadence_preview` (item 4.3): the numbers the engine plans with, not a promise.
export function cadenceHint(
  rows: CadencePreview[] | undefined,
  persona: ActivityPersona,
): { first: string; last: string } | null {
  const own = (rows ?? []).filter((row) => row.persona === persona);
  const first = own.find((row) => row.phase === 'intro');
  const last = own.find((row) => row.phase === 'warmed');
  if (!first || !last) return null;
  return {
    first: span(first.visits_min, first.visits_max),
    last: span(last.visits_min, last.visits_max),
  };
}
