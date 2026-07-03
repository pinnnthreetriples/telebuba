import { expect, test } from 'vitest';

import { formatLocalTime } from './formatTime';

// The exact hours digits depend on the runtime's time zone ("local time"), so
// assert on the segment count (hours:minutes[:seconds]). The clock is forced to
// 24-hour (hour12:false), so there is never an AM/PM suffix regardless of locale.
test('formats a valid ISO timestamp with hours and minutes by default', () => {
  expect(formatLocalTime('2026-07-01T14:00:00Z').split(':')).toHaveLength(2);
});

test('uses a 24-hour clock (no AM/PM suffix)', () => {
  expect(formatLocalTime('2026-07-01T14:00:00Z')).not.toMatch(/[ap]m/i);
  expect(formatLocalTime('2026-07-01T02:00:00Z')).not.toMatch(/[ap]m/i);
});

test('includes seconds when requested', () => {
  expect(formatLocalTime('2026-07-01T14:00:00Z', { seconds: true }).split(':')).toHaveLength(3);
});

test('falls back to the raw string for an unparseable timestamp', () => {
  expect(formatLocalTime('not-a-date')).toBe('not-a-date');
});
