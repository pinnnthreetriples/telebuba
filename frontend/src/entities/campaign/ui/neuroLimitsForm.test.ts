import { expect, test } from 'vitest';

import type { NeurocommentSettings } from '@/shared/api';

import { neuroLimitsErrors, neuroLimitsValue } from './neuroLimitsForm';

const STORED: NeurocommentSettings = {
  max_comments_per_hour: 10,
  max_comments_per_channel_per_day: 3,
  reply_delay_min_seconds: 3,
  reply_delay_max_seconds: 10,
  min_trust_score: 45,
  comment_mode: 'first',
  reply_wait_minutes: 10,
  updated_at: 'now',
};

// `String(n)` renders these in exponent form; an untouched field must never block Save.
test.each([
  [1e-7, 1e21],
  [0, 1e-7],
  [2.5, 7200],
])('a stored delay pair %s..%s round-trips as valid', (from, to) => {
  const value = neuroLimitsValue({
    ...STORED,
    reply_delay_min_seconds: from,
    reply_delay_max_seconds: to,
  });
  expect(neuroLimitsErrors(value)).toEqual({});
});

test.each(['', ' ', '-1', 'abc', 'Infinity', 'NaN', '1e400'])('delay %j is refused', (raw) => {
  const value = { ...neuroLimitsValue(STORED), delayFrom: raw };
  expect(neuroLimitsErrors(value).delayFrom).toBe('neurocomment.limits.errDelay');
});

test('a delay order check still compares exponent-form numbers numerically', () => {
  const value = { ...neuroLimitsValue(STORED), delayFrom: '1e3', delayTo: '20' };
  expect(neuroLimitsErrors(value)).toEqual({ delayTo: 'neurocomment.limits.errDelayOrder' });
});

// Past 2^53 `Number()` loses the value (and `'9'.repeat(400)` becomes Infinity, which
// JSON sends as null) — a "saved" that changed nothing.
test.each([
  ['cpd', 'neurocomment.limits.errCpd'],
  ['parallel', 'neurocomment.limits.errParallel'],
  ['trust', 'neurocomment.limits.errTrust'],
] as const)('an integer %s beyond the safe range is refused', (fieldName, message) => {
  for (const raw of ['9'.repeat(400), '9007199254740993']) {
    const value = { ...neuroLimitsValue(STORED), [fieldName]: raw };
    expect(neuroLimitsErrors(value)[fieldName]).toBe(message);
  }
});
