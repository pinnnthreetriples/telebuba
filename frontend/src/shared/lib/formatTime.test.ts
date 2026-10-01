import { expect, test } from 'vitest';

import { formatLocalDateTime, formatLocalTime, formatRelativeTo } from './formatTime';

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

test('a date-time carries the day as well as a 24-hour time', () => {
  const text = formatLocalDateTime(Date.UTC(2026, 9, 3, 14, 30), 'en');
  expect(text).toMatch(/Oct/);
  expect(text).not.toMatch(/[ap]m/i);
  expect(formatLocalDateTime(Number.NaN, 'en')).toBe('');
});

test('relative time picks minutes, hours or days by distance', () => {
  const now = Date.UTC(2026, 0, 1);
  expect(formatRelativeTo(now + 5 * 60_000, now, 'en')).toBe('in 5 minutes');
  expect(formatRelativeTo(now + 3 * 3_600_000, now, 'en')).toBe('in 3 hours');
  expect(formatRelativeTo(now + 3 * 86_400_000, now, 'en')).toBe('in 3 days');
  expect(formatRelativeTo(now - 2 * 60_000, now, 'en')).toBe('2 minutes ago');
});
