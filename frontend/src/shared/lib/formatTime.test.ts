import { expect, test } from 'vitest';

import { formatLocalTime } from './formatTime';

test('formats a valid ISO timestamp as HH:MM by default', () => {
  expect(formatLocalTime('2026-07-01T14:00:00Z')).toMatch(/^\d{1,2}:\d{2}$/);
});

test('includes seconds when requested', () => {
  expect(formatLocalTime('2026-07-01T14:00:00Z', { seconds: true })).toMatch(
    /^\d{1,2}:\d{2}:\d{2}$/,
  );
});

test('falls back to the raw string for an unparseable timestamp', () => {
  expect(formatLocalTime('not-a-date')).toBe('not-a-date');
});
