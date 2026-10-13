import { expect, test } from 'vitest';

import { headlineWindow, isoWeekNumber, PERCENT_FROM, shareOf } from './share';

test('a share counts alive against alive plus dead, never the unchecked', () => {
  expect(shareOf({ window: 'd7', alive: 3, dead: 1, unchecked: 5 })).toEqual({
    alive: 3,
    decided: 4,
    percent: null,
  });
  expect(shareOf(undefined)).toEqual({ alive: 0, decided: 0, percent: null });
});

test(`a percentage appears only from ${String(PERCENT_FROM)} decided accounts`, () => {
  expect(shareOf({ window: 'd7', alive: 8, dead: 1 }).percent).toBeNull();
  expect(shareOf({ window: 'd7', alive: 7, dead: 3 }).percent).toBe(70);
});

test('day seven heads the tile until day thirty has enough answers', () => {
  const thin = { windows: [{ window: 'd30' as const, alive: 2, dead: 1 }] };
  const full = { windows: [{ window: 'd30' as const, alive: 8, dead: 2 }] };
  expect(headlineWindow(thin)).toBe('d7');
  expect(headlineWindow(full)).toBe('d30');
  expect(headlineWindow(undefined)).toBe('d7');
});

test('an ISO week key reads as its number', () => {
  expect(isoWeekNumber('2026-W07')).toBe('7');
  expect(isoWeekNumber('later')).toBe('later');
});
