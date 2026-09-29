import { z } from 'zod';

import type { NeurocommentSettings, NeurocommentSettingsUpdate } from '@/shared/api';

// Value type + zod schema for the fleet-wide neurocomment limits, the "Лимиты" tab of the
// listener modal. Kept out of the .tsx so that file only exports components
// (react-refresh/only-export-components). Fields are strings (raw input); the schema
// coerces and bounds them so an empty field is an error rather than a silently-sent 0.
//
// The bounds are the backend's own (`NeurocommentSettingsUpdate`), restated because the
// generated client carries none — and no tighter: a stricter form rejected values the
// engine already held (0 = no per-channel cap, a fractional delay), which made the form
// invalid the moment it opened and left Save disabled for good.
export interface NeuroLimitsValue {
  cpd: string;
  delayFrom: string;
  delayTo: string;
  parallel: string;
  trust: string;
}

export type NeuroLimitsField = keyof NeuroLimitsValue;

export function neuroLimitsValue(s: NeurocommentSettings): NeuroLimitsValue {
  return {
    cpd: String(s.max_comments_per_channel_per_day),
    delayFrom: String(s.reply_delay_min_seconds),
    delayTo: String(s.reply_delay_max_seconds),
    parallel: String(s.max_comments_per_hour),
    trust: String(s.min_trust_score),
  };
}

const INTEGER = /^\d+$/;

// Parsed, not pattern-matched: `neuroLimitsValue` renders a stored 1e-7 or 1e21 in
// exponent form, and an untouched field must stay valid.
const nonNegative = (value: string) => {
  if (value.trim() === '') return false;
  const n = Number(value);
  return Number.isFinite(n) && n >= 0;
};

// One refine per field so it emits exactly ONE issue: the error line shows the first only.
// Safe integers only: past 2^53 `Number()` drifts, and '9'.repeat(400) is Infinity, which
// JSON sends as null — a "saved" that changed nothing.
const intAtLeast = (min: number, message: string, max = Infinity) =>
  z.string().refine((value) => {
    if (!INTEGER.test(value)) return false;
    const n = Number(value);
    return Number.isSafeInteger(n) && n >= min && n <= max;
  }, message);

const seconds = z.string().refine(nonNegative, 'neurocomment.limits.errDelay');

export const neuroLimitsSchema = z
  .object({
    cpd: intAtLeast(0, 'neurocomment.limits.errCpd'),
    delayFrom: seconds,
    delayTo: seconds,
    parallel: intAtLeast(1, 'neurocomment.limits.errParallel'),
    trust: intAtLeast(0, 'neurocomment.limits.errTrust', 100),
  })
  .refine(
    (v) =>
      // Only once both are valid numbers — otherwise the per-field error already covers it.
      !nonNegative(v.delayFrom) ||
      !nonNegative(v.delayTo) ||
      Number(v.delayFrom) <= Number(v.delayTo),
    { message: 'neurocomment.limits.errDelayOrder', path: ['delayTo'] },
  );

// First message per field, as i18n keys — `{}` when the value is valid.
export function neuroLimitsErrors(v: NeuroLimitsValue): Partial<Record<NeuroLimitsField, string>> {
  const parsed = neuroLimitsSchema.safeParse(v);
  if (parsed.success) return {};
  const errors: Partial<Record<NeuroLimitsField, string>> = {};
  for (const issue of parsed.error.issues) {
    const field = issue.path[0] as NeuroLimitsField;
    errors[field] ??= issue.message;
  }
  return errors;
}

// The patch for a VALID draft: only the fields the operator TOUCHED, and of those only
// what differs from the stored row — every field of the route is patch-shaped, and an
// untouched field sent back from a snapshot would roll back a save made elsewhere. The
// delay pair travels together (the backend checks min ≤ max on the wire), its untouched
// half read fresh from `stored`.
export function neuroLimitsPatch(
  touched: Partial<NeuroLimitsValue>,
  stored: NeurocommentSettings,
): Partial<NeurocommentSettingsUpdate> {
  const patch: Partial<NeurocommentSettingsUpdate> = {};
  const v = { ...neuroLimitsValue(stored), ...touched };
  const cpd = Number(v.cpd);
  const from = Number(v.delayFrom);
  const to = Number(v.delayTo);
  const parallel = Number(v.parallel);
  const trust = Number(v.trust);
  if (touched.cpd !== undefined && cpd !== stored.max_comments_per_channel_per_day) {
    patch.max_comments_per_channel_per_day = cpd;
  }
  if (
    (touched.delayFrom !== undefined || touched.delayTo !== undefined) &&
    (from !== stored.reply_delay_min_seconds || to !== stored.reply_delay_max_seconds)
  ) {
    patch.reply_delay_min_seconds = from;
    patch.reply_delay_max_seconds = to;
  }
  if (touched.parallel !== undefined && parallel !== stored.max_comments_per_hour) {
    patch.max_comments_per_hour = parallel;
  }
  if (touched.trust !== undefined && trust !== stored.min_trust_score) {
    patch.min_trust_score = trust;
  }
  return patch;
}
