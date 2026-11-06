import { expect, test } from 'vitest';

import {
  clamp,
  clampCenter,
  clampScale,
  distance,
  normalizeAngle,
  nudge,
  rotationFromHandle,
  scaleFromDrag,
} from './geometry';

test('clamp bounds a number', () => {
  expect(clamp(5, 0, 3)).toBe(3);
  expect(clamp(-1, 0, 3)).toBe(0);
  expect(clamp(2, 0, 3)).toBe(2);
});

test('angles wrap into (-180, 180]', () => {
  expect(normalizeAngle(190)).toBe(-170);
  expect(normalizeAngle(-190)).toBe(170);
  expect(normalizeAngle(180)).toBe(180);
  expect(normalizeAngle(-180)).toBe(180);
  expect(normalizeAngle(720)).toBe(0);
  expect(normalizeAngle(0)).toBe(0);
});

test('the centre is clamped so the whole box stays on the canvas', () => {
  const canvas = { width: 200, height: 400 };
  const box = { width: 100, height: 40 };
  expect(clampCenter({ x: 0, y: 0 }, box, canvas)).toEqual({ x: 25, y: 5 });
  expect(clampCenter({ x: 100, y: 100 }, box, canvas)).toEqual({ x: 75, y: 95 });
  expect(clampCenter({ x: 40, y: 50 }, box, canvas)).toEqual({ x: 40, y: 50 });
});

test('a box wider than the canvas is centred; an unmeasured canvas just clamps to 0..100', () => {
  const wide = clampCenter(
    { x: 10, y: 10 },
    { width: 300, height: 10 },
    { width: 200, height: 400 },
  );
  expect(wide.x).toBe(50);
  const unmeasured = clampCenter(
    { x: 140, y: -5 },
    { width: 0, height: 0 },
    { width: 0, height: 0 },
  );
  expect(unmeasured).toEqual({ x: 100, y: 0 });
});

test('the rotate handle gives degrees clockwise from straight up', () => {
  const centre = { x: 100, y: 100 };
  expect(rotationFromHandle(centre, { x: 100, y: 0 })).toBe(0);
  expect(rotationFromHandle(centre, { x: 200, y: 100 })).toBe(90);
  expect(rotationFromHandle(centre, { x: 100, y: 200 })).toBe(180);
  expect(rotationFromHandle(centre, { x: 0, y: 100 })).toBe(-90);
});

test('Shift snaps the rotation to 15 degrees', () => {
  const centre = { x: 0, y: 0 };
  const radians = (20 * Math.PI) / 180;
  const pointer = { x: Math.sin(radians) * 50, y: -Math.cos(radians) * 50 };
  expect(rotationFromHandle(centre, pointer, true)).toBe(15);
  expect(rotationFromHandle(centre, pointer)).toBeCloseTo(20, 0);
});

test('scale follows the pointer distance and stays within 0.5..2.5', () => {
  expect(scaleFromDrag(1, 50, 100)).toBe(2);
  expect(scaleFromDrag(1, 50, 25)).toBe(0.5);
  expect(scaleFromDrag(1, 50, 1000)).toBe(2.5);
  expect(scaleFromDrag(1, 50, 1)).toBe(0.5);
  expect(scaleFromDrag(3, 0, 10)).toBe(2.5);
  expect(clampScale(0.1)).toBe(0.5);
});

test('arrow keys nudge by 1%, Shift by 5%, other keys do nothing', () => {
  const at = { x: 50, y: 50 };
  expect(nudge(at, 'ArrowLeft', false)).toEqual({ x: 49, y: 50 });
  expect(nudge(at, 'ArrowRight', true)).toEqual({ x: 55, y: 50 });
  expect(nudge(at, 'ArrowUp', false)).toEqual({ x: 50, y: 49 });
  expect(nudge(at, 'ArrowDown', true)).toEqual({ x: 50, y: 55 });
  expect(nudge(at, 'a', false)).toBeNull();
});

test('distance is euclidean', () => {
  expect(distance({ x: 0, y: 0 }, { x: 3, y: 4 })).toBe(5);
});
