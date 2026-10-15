import type { CadencePreview, WarmingTuning } from '@/shared/api';

export type TuningPreset = WarmingTuning['preset'];
type Phase = CadencePreview['phase'];
type Persona = CadencePreview['persona'];

export const PRESETS: readonly TuningPreset[] = ['careful', 'normal', 'fast', 'custom'];
export const PHASES: readonly Phase[] = ['intro', 'settling', 'warming', 'active', 'warmed'];
export const PERSONAS: readonly Persona[] = ['calm', 'normal', 'active'];
// The bounds the server holds a «Свой» limit to (`schemas._warming_tuning.DailyCap`).
export const CAP_MIN = 1;
export const CAP_MAX = 80;

export interface PaceRow {
  phase: Phase;
  cap: number;
  // Persona → «5–6» visits a day, the 4.3 cadence math under these limits.
  visits: Record<Persona, string>;
}

function span(min: number, max: number): string {
  return min === max ? String(min) : `${String(min)}–${String(max)}`;
}

// One row per phase: its daily limit and the visits a day each persona makes under it.
export function paceRows(tuning: WarmingTuning): PaceRow[] {
  return PHASES.map((phase) => {
    const visits = Object.fromEntries(
      PERSONAS.map((persona) => {
        const row = (tuning.cadence ?? []).find((r) => r.phase === phase && r.persona === persona);
        return [persona, row ? span(row.visits_min, row.visits_max) : '—'];
      }),
    ) as Record<Persona, string>;
    return { phase, cap: tuning.daily_caps[phase] ?? 0, visits };
  });
}

// What «Свой» starts from: the limits saved last time, else the ones in force.
export function draftCaps(tuning: WarmingTuning): Record<Phase, number> {
  const source = tuning.custom?.daily_caps ?? tuning.daily_caps;
  return Object.fromEntries(PHASES.map((p) => [p, source[p] ?? CAP_MIN])) as Record<Phase, number>;
}

// The server's own rules for «Свой», checked before the round trip: every limit a whole
// number in 1..80, and never lower than the phase before it.
export function capsValid(caps: Record<Phase, number>): boolean {
  const values = PHASES.map((p) => caps[p]);
  const inRange = values.every((v) => Number.isInteger(v) && v >= CAP_MIN && v <= CAP_MAX);
  return inRange && values.every((v, i) => i === 0 || v >= (values[i - 1] ?? v));
}
