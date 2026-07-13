import { expect, test } from 'vitest';

import {
  COLLAGE_LAYOUTS,
  defaultLayoutId,
  isLayoutValidForCount,
  layoutsForCount,
} from './storyCollageLayouts';

const THIRD = 1 / 3;

// Byte-for-byte mirror of the backend `_COLLAGE_TEMPLATES`. A mismatch here
// means the SPA can send a layout id the backend rejects with
// `story_collage_unknown_layout`, so this is a hard regression guard.
const EXPECTED: Record<number, Record<string, number[][]>> = {
  2: {
    v2: [
      [0, 0, 1, 0.5],
      [0, 0.5, 1, 0.5],
    ],
    h2: [
      [0, 0, 0.5, 1],
      [0.5, 0, 0.5, 1],
    ],
  },
  3: {
    v3: [
      [0, 0, 1, THIRD],
      [0, THIRD, 1, THIRD],
      [0, 2 * THIRD, 1, THIRD],
    ],
    left1_right2: [
      [0, 0, 0.5, 1],
      [0.5, 0, 0.5, 0.5],
      [0.5, 0.5, 0.5, 0.5],
    ],
    top1_bottom2: [
      [0, 0, 1, 0.5],
      [0, 0.5, 0.5, 0.5],
      [0.5, 0.5, 0.5, 0.5],
    ],
  },
  4: {
    grid2x2: [
      [0, 0, 0.5, 0.5],
      [0.5, 0, 0.5, 0.5],
      [0, 0.5, 0.5, 0.5],
      [0.5, 0.5, 0.5, 0.5],
    ],
    v4: [
      [0, 0, 1, 0.25],
      [0, 0.25, 1, 0.25],
      [0, 0.5, 1, 0.25],
      [0, 0.75, 1, 0.25],
    ],
  },
  5: {
    top2_bottom3: [
      [0, 0, 0.5, 0.5],
      [0.5, 0, 0.5, 0.5],
      [0, 0.5, THIRD, 0.5],
      [THIRD, 0.5, THIRD, 0.5],
      [2 * THIRD, 0.5, THIRD, 0.5],
    ],
  },
  6: {
    grid2x3: [
      [0, 0, 0.5, THIRD],
      [0.5, 0, 0.5, THIRD],
      [0, THIRD, 0.5, THIRD],
      [0.5, THIRD, 0.5, THIRD],
      [0, 2 * THIRD, 0.5, THIRD],
      [0.5, 2 * THIRD, 0.5, THIRD],
    ],
  },
};

test('collage templates match the backend byte-for-byte', () => {
  const actual = Object.fromEntries(
    Object.entries(COLLAGE_LAYOUTS).map(([count, layouts]) => [
      count,
      Object.fromEntries(layouts.map((l) => [l.id, l.cells.map((c) => [...c])])),
    ]),
  );
  expect(actual).toEqual(EXPECTED);
});

test('every cell rectangle stays within the unit canvas', () => {
  for (const layouts of Object.values(COLLAGE_LAYOUTS)) {
    for (const { cells } of layouts) {
      for (const [x, y, w, h] of cells) {
        expect(x).toBeGreaterThanOrEqual(0);
        expect(y).toBeGreaterThanOrEqual(0);
        expect(x + w).toBeLessThanOrEqual(1 + 1e-9);
        expect(y + h).toBeLessThanOrEqual(1 + 1e-9);
      }
    }
  }
});

test('the number of cells equals the photo count for every layout', () => {
  for (const [count, layouts] of Object.entries(COLLAGE_LAYOUTS)) {
    for (const layout of layouts) {
      expect(layout.cells).toHaveLength(Number(count));
    }
  }
});

test('defaultLayoutId returns the first template, or null when unsupported', () => {
  expect(defaultLayoutId(2)).toBe('v2');
  expect(defaultLayoutId(3)).toBe('v3');
  expect(defaultLayoutId(1)).toBeNull();
  expect(defaultLayoutId(7)).toBeNull();
});

test('layoutsForCount is empty for unsupported counts', () => {
  expect(layoutsForCount(1)).toEqual([]);
  expect(layoutsForCount(7)).toEqual([]);
  expect(layoutsForCount(4)).toHaveLength(2);
});

test('isLayoutValidForCount guards id + count together', () => {
  expect(isLayoutValidForCount('v2', 2)).toBe(true);
  expect(isLayoutValidForCount('h2', 2)).toBe(true);
  expect(isLayoutValidForCount('v2', 3)).toBe(false);
  expect(isLayoutValidForCount('grid2x2', 4)).toBe(true);
  expect(isLayoutValidForCount(null, 2)).toBe(false);
  expect(isLayoutValidForCount('nope', 2)).toBe(false);
});
