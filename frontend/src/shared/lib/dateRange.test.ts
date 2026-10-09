import { expect, test } from 'vitest';

import {
  addDays,
  addMonths,
  clampRangeEnd,
  compareDays,
  firstEnabledDayInMonth,
  getMonthGrid,
  isSameDay,
  isWithinRange,
  nightsBetween,
  startOfDay,
  startOfMonth,
} from './dateRange';

const day = (m: number, d: number) => new Date(2026, m - 1, d);

test('days ignore the time of day', () => {
  const late = new Date(2026, 9, 6, 23, 59);
  expect(startOfDay(late)).toEqual(day(10, 6));
  expect(isSameDay(late, day(10, 6))).toBe(true);
  expect(compareDays(late, day(10, 6))).toBe(0);
  expect(compareDays(day(10, 5), late)).toBe(-1);
  expect(compareDays(day(10, 7), late)).toBe(1);
  expect(startOfMonth(late)).toEqual(day(10, 1));
});

test('adding days and months crosses month and year edges', () => {
  expect(addDays(day(10, 30), 3)).toEqual(day(11, 2));
  expect(addDays(day(1, 1), -1)).toEqual(new Date(2025, 11, 31));
  expect(addMonths(day(12, 15), 1)).toEqual(new Date(2027, 0, 1));
  expect(addMonths(day(3, 31), -1)).toEqual(day(2, 1));
});

test('a range includes both ends and counts nights between them', () => {
  expect(isWithinRange(day(10, 6), day(10, 6), day(10, 9))).toBe(true);
  expect(isWithinRange(day(10, 9), day(10, 6), day(10, 9))).toBe(true);
  expect(isWithinRange(day(10, 10), day(10, 6), day(10, 9))).toBe(false);
  expect(nightsBetween(day(10, 6), day(10, 9))).toBe(3);
  // Across the end of daylight saving in most zones: still whole nights.
  expect(nightsBetween(day(10, 20), day(11, 3))).toBe(14);
});

test('a range stops short of the first disabled day, in either direction', () => {
  const closed = (d: Date) => isSameDay(d, day(10, 10));
  expect(clampRangeEnd(day(10, 6), day(10, 14), closed)).toEqual(day(10, 9));
  expect(clampRangeEnd(day(10, 14), day(10, 6), closed)).toEqual(day(10, 11));
  expect(clampRangeEnd(day(10, 6), day(10, 8), closed)).toEqual(day(10, 8));
  expect(clampRangeEnd(day(10, 6), new Date(2026, 9, 8, 15))).toEqual(day(10, 8));
});

test('the month grid is whole Monday-first weeks padded with neighbours', () => {
  // October 2026 starts on a Thursday and ends on a Saturday.
  const weeks = getMonthGrid(day(10, 17));
  expect(weeks).toHaveLength(5);
  for (const week of weeks) expect(week).toHaveLength(7);
  expect(weeks[0]?.[0]).toEqual({ date: day(9, 28), inMonth: false });
  expect(weeks[0]?.[3]).toEqual({ date: day(10, 1), inMonth: true });
  expect(weeks[4]?.[6]).toEqual({ date: day(11, 1), inMonth: false });
  expect(weeks.flat().filter((d) => d.inMonth)).toHaveLength(31);
});

test('the first enabled day of a month, or none', () => {
  const beforeTenth = (d: Date) => d.getDate() < 10;
  expect(firstEnabledDayInMonth(day(10, 20), beforeTenth)).toEqual(day(10, 10));
  expect(firstEnabledDayInMonth(day(10, 20), () => true)).toBeNull();
});
