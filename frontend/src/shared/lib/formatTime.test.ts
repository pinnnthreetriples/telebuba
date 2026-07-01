import { expect, test } from 'vitest';

import { formatLocalTime } from './formatTime';

// toLocaleTimeString's hour cycle and AM/PM suffix depend on the runtime's
// default locale (12-hour with "PM" on this CI's Linux/ICU vs. 24-hour on a
// typical Windows dev box) — that's the point of "local time", so assert on
// the segment count (hours:minutes[:seconds]) rather than an exact format.
test('formats a valid ISO timestamp with hours and minutes by default', () => {
  expect(formatLocalTime('2026-07-01T14:00:00Z').split(':')).toHaveLength(2);
});

test('includes seconds when requested', () => {
  expect(formatLocalTime('2026-07-01T14:00:00Z', { seconds: true }).split(':')).toHaveLength(3);
});

test('falls back to the raw string for an unparseable timestamp', () => {
  expect(formatLocalTime('not-a-date')).toBe('not-a-date');
});
