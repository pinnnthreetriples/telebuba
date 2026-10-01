import { expect, test } from 'vitest';

import {
  bulkRunAts,
  clampToLead,
  defaultRunAt,
  fromLocalInput,
  MAX_LEAD_MS,
  runAtProblem,
  spreadEvery,
  toIso,
  toLocalInput,
} from './runAt';

const MINUTE = 60_000;
const NOW = Date.UTC(2026, 9, 1, 10, 2, 30);

test('a local input value round-trips to the same minute', () => {
  const ms = Date.UTC(2026, 9, 3, 14, 30);
  expect(fromLocalInput(toLocalInput(ms))).toBe(ms);
  expect(toLocalInput(ms)).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
});

test('an empty or garbled input value is no moment at all', () => {
  expect(fromLocalInput('')).toBeNull();
  expect(fromLocalInput('not-a-date')).toBeNull();
});

test('the default suggestion is at least the lead away and on a five-minute mark', () => {
  const suggested = defaultRunAt(NOW);
  expect(suggested - NOW).toBeGreaterThanOrEqual(15 * MINUTE);
  expect(suggested % (5 * MINUTE)).toBe(0);
});

test('the window refuses empty, too soon and too far', () => {
  expect(runAtProblem(null, NOW)).toBe('empty');
  expect(runAtProblem(NOW + 30_000, NOW)).toBe('past');
  expect(runAtProblem(NOW + MAX_LEAD_MS + MINUTE, NOW)).toBe('tooFar');
  expect(runAtProblem(NOW + 2 * MINUTE, NOW)).toBeNull();
  expect(runAtProblem(NOW + 2 * MINUTE, NOW, 5 * MINUTE)).toBe('past');
});

test('spreading puts one moment every step, starting at the start', () => {
  expect(spreadEvery(NOW, 3, 30)).toEqual([NOW, NOW + 30 * MINUTE, NOW + 60 * MINUTE]);
  expect(spreadEvery(NOW, 0, 30)).toEqual([]);
});

test('bulk moments keep the account order and stay within half a gap of the grid', () => {
  const plan = bulkRunAts(NOW, 4, 10, () => 0.999);
  plan.forEach((ms, index) => {
    const grid = NOW + index * 10 * MINUTE;
    expect(ms).toBeGreaterThanOrEqual(grid);
    expect(ms).toBeLessThan(grid + 5 * MINUTE);
  });
  expect([...plan].sort((a, b) => a - b)).toEqual(plan);
  expect(bulkRunAts(NOW, 3, 0, () => 0.5)).toEqual([NOW, NOW, NOW]);
});

test('a moment time has caught up with is pushed back into the window', () => {
  expect(clampToLead(NOW - MINUTE, NOW)).toBe(NOW + 90_000);
  expect(clampToLead(NOW + 10 * MINUTE, NOW)).toBe(NOW + 10 * MINUTE);
});

test('the wire form is an aware UTC ISO string', () => {
  expect(toIso(Date.UTC(2026, 0, 2, 3, 4))).toBe('2026-01-02T03:04:00.000Z');
});
