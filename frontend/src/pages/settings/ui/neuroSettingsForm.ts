import { z } from 'zod';

import type { NeurocommentSettings } from '@/shared/api';

// Value type + zod schema for the neurocomment-limits form. Kept out of
// SettingsPage.tsx so that file only exports components
// (react-refresh/only-export-components). Fields are strings (raw input); the
// schema coerces to numbers and enforces min/max so an empty field is a
// validation error rather than a silently-sent 0, and non-numeric input is
// blocked before mutateAsync instead of surfacing as a generic 422.
export interface NeuroFormValue {
  cpd: string;
  delayFrom: string;
  delayTo: string;
  parallel: string;
  trust: string;
}

export function neuroFormValue(s: NeurocommentSettings): NeuroFormValue {
  return {
    cpd: String(s.max_comments_per_channel_per_day),
    delayFrom: String(s.reply_delay_min_seconds),
    delayTo: String(s.reply_delay_max_seconds),
    parallel: String(s.max_comments_per_hour),
    trust: String(s.min_trust_score),
  };
}

// An integer within [min, max]. A single refine (not regex + refine) so the field
// emits exactly ONE issue. The original reason expired with react-form 1.x: 0.x
// comma-joined a field's issues into one string, which destroyed the i18n-key →
// t() resolution in FieldError, and 1.x keeps them as separate entries. What still
// holds is weaker but real — `FieldError` renders `errors[0]` only, so a second
// issue would be silently dropped rather than mangled. Empty / non-numeric input
// and out-of-range therefore share one message on purpose.
const intInRange = (min: number, max: number, message: string) =>
  z.string().refine((value) => {
    if (!/^\d+$/.test(value)) return false;
    const n = Number(value);
    return n >= min && n <= max;
  }, message);

export const neuroFormSchema = z
  .object({
    cpd: intInRange(1, 100, 'settings.neuroLimits.errCpd'),
    delayFrom: intInRange(0, 3600, 'settings.neuroLimits.errDelay'),
    delayTo: intInRange(0, 3600, 'settings.neuroLimits.errDelay'),
    parallel: intInRange(1, 1000, 'settings.neuroLimits.errParallel'),
    trust: intInRange(0, 100, 'settings.neuroLimits.errTrust'),
  })
  .refine(
    (v) => {
      // Only compare once both delay fields are valid integers — otherwise the
      // per-field error already covers it, and the second issue this would add on
      // delayTo would lose to it anyway (FieldError shows errors[0]).
      const from = /^\d+$/.test(v.delayFrom) ? Number(v.delayFrom) : NaN;
      const to = /^\d+$/.test(v.delayTo) ? Number(v.delayTo) : NaN;
      return Number.isNaN(from) || Number.isNaN(to) || from <= to;
    },
    { message: 'settings.neuroLimits.errDelayOrder', path: ['delayTo'] },
  );

// The neurocomment-settings update body derived from a validated form value.
export function neuroUpdateBody(v: NeuroFormValue) {
  return {
    max_comments_per_channel_per_day: Number(v.cpd),
    reply_delay_min_seconds: Number(v.delayFrom),
    reply_delay_max_seconds: Number(v.delayTo),
    max_comments_per_hour: Number(v.parallel),
    min_trust_score: Number(v.trust),
  };
}
