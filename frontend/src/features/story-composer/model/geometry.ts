import { STICKER_SCALE_MAX, STICKER_SCALE_MIN } from './limits';

// The 9:16 canvas, and the plate the server draws on it: its height is this share of the
// canvas height at scale 1.
export const CANVAS_ASPECT = '9 / 16';
export const PLATE_HEIGHT_RATIO = 0.06;
export const NUDGE_STEP = 1;
export const NUDGE_STEP_LARGE = 5;
export const SCALE_KEY_STEP = 0.1;
export const ROTATE_KEY_STEP = 5;
export const ROTATE_SNAP = 15;

export interface Point {
  x: number;
  y: number;
}
export interface Size {
  width: number;
  height: number;
}

export const clamp = (value: number, min: number, max: number): number =>
  Math.min(max, Math.max(min, value));

export const clampScale = (scale: number): number =>
  clamp(scale, STICKER_SCALE_MIN, STICKER_SCALE_MAX);

// Wrap an angle in degrees into (-180, 180].
export function normalizeAngle(degrees: number): number {
  const wrapped = ((((degrees + 180) % 360) + 360) % 360) - 180;
  return wrapped === -180 ? 180 : wrapped;
}

// Keep the whole plate on the canvas: `box` is the plate's on-screen bounding box in
// px (rotation and scale already in it), `canvas` the canvas in px. A box wider than
// the canvas is centred.
export function clampCenter(center: Point, box: Size, canvas: Size): Point {
  const limit = (value: number, extent: number, total: number) => {
    if (total <= 0) return clamp(value, 0, 100);
    const half = (extent / total / 2) * 100;
    return half >= 50 ? 50 : clamp(value, half, 100 - half);
  };
  return {
    x: limit(center.x, box.width, canvas.width),
    y: limit(center.y, box.height, canvas.height),
  };
}

// Angle of the rotate handle: degrees clockwise from straight up, with the sticker's
// centre as the pivot. Holding Shift snaps to `ROTATE_SNAP`.
export function rotationFromHandle(center: Point, pointer: Point, snap = false): number {
  const dx = pointer.x - center.x;
  const dy = pointer.y - center.y;
  const degrees = (Math.atan2(dx, -dy) * 180) / Math.PI;
  const value = snap ? Math.round(degrees / ROTATE_SNAP) * ROTATE_SNAP : degrees;
  return normalizeAngle(Math.round(value * 10) / 10);
}

// Scale follows how far the pointer is from the centre relative to where the drag began.
export function scaleFromDrag(startScale: number, startDistance: number, distance: number): number {
  if (startDistance <= 0) return clampScale(startScale);
  return clampScale(Math.round(startScale * (distance / startDistance) * 100) / 100);
}

export const distance = (a: Point, b: Point): number => Math.hypot(a.x - b.x, a.y - b.y);

const ARROWS: Record<string, Point> = {
  ArrowLeft: { x: -1, y: 0 },
  ArrowRight: { x: 1, y: 0 },
  ArrowUp: { x: 0, y: -1 },
  ArrowDown: { x: 0, y: 1 },
};

// Arrow-key nudge in percent; null for any other key.
export function nudge(center: Point, key: string, large: boolean): Point | null {
  const direction = ARROWS[key];
  if (!direction) return null;
  const step = large ? NUDGE_STEP_LARGE : NUDGE_STEP;
  return { x: center.x + direction.x * step, y: center.y + direction.y * step };
}
