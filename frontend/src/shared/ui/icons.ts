// The shapes behind <Icon>, keyed by name. Non-component module for the same reason
// _styles.ts is one: `react-refresh/only-export-components` allows a constant export
// only for a literal, not a table, and Icon.tsx has to stay components-only.
//
// Only glyphs the app draws more than once are here. Roughly thirty shapes are drawn
// exactly once and stay inline at their call site: a registry entry for one of them
// would be a name to look up with a single caller on the other side.
//
// Circles and rects stay circles and rects instead of being folded into path data.
// Eight of these are built from them, and an arc-encoded `d` is not something a
// reviewer can check against the icon it replaced.
type Part =
  | { d: string }
  | { cx: number; cy: number; r: number }
  | { x: number; y: number; width: number; height: number; rx: number };

export type IconDef = { parts: Part[]; fill?: boolean };

// `fill` icons are solid silhouettes and take no stroke at all — the four play
// triangles and the four pause bars. Everything else is a 24-unit outline.
export const ICONS = {
  'alert-square': {
    parts: [{ x: 3, y: 3, width: 18, height: 18, rx: 2 }, { d: 'M12 7v2M12 12v2M12 17v.5' }],
  },
  'alert-triangle': {
    parts: [
      {
        d: 'M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
      },
    ],
  },
  'arrow-right': { parts: [{ d: 'M5 12h14M13 6l6 6-6 6' }] },
  chart: {
    parts: [
      { d: 'M2 10v3' },
      { d: 'M6 6v11' },
      { d: 'M10 3v18' },
      { d: 'M14 8v7' },
      { d: 'M18 5v13' },
      { d: 'M22 10v3' },
    ],
  },
  check: { parts: [{ d: 'M20 6 9 17l-5-5' }] },
  'check-circle': { parts: [{ cx: 12, cy: 12, r: 10 }, { d: 'm8 12 2.5 2.5L16 9' }] },
  'chevron-down': { parts: [{ d: 'm6 9 6 6 6-6' }] },
  'chevron-right': { parts: [{ d: 'm9 18 6-6-6-6' }] },
  // The app spelled this one as a single two-stroke path in six files and as two
  // separate paths in two more. Same rendered cross, so one entry.
  close: { parts: [{ d: 'M18 6 6 18M6 6l12 12' }] },
  eye: {
    parts: [{ d: 'M2 12s3-8 10-8 10 8 10 8-3 8-10 8-10-8-10-8z' }, { cx: 12, cy: 12, r: 3 }],
  },
  'eye-off': {
    parts: [
      { d: 'M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 10 8 10 8a13.16 13.16 0 0 1-1.67 2.68' },
      { d: 'M6.61 6.61A13.5 13.5 0 0 0 2 12s3 8 10 8a9.12 9.12 0 0 0 5.39-1.61' },
      { d: 'M14.12 14.12A3 3 0 1 1 9.88 9.88' },
      { d: 'M1 1l22 22' },
    ],
  },
  file: {
    parts: [
      { d: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z' },
      { d: 'M14 2v6h6' },
    ],
  },
  gear: {
    parts: [
      { cx: 12, cy: 12, r: 3 },
      {
        d: 'M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z',
      },
    ],
  },
  // Two of the four sites drew these bars with `rx: 1`. 1.5 wins because it is the
  // radius the small rung used, where a 1-unit corner all but disappears.
  pause: {
    parts: [
      { x: 6, y: 5, width: 4, height: 14, rx: 1.5 },
      { x: 14, y: 5, width: 4, height: 14, rx: 1.5 },
    ],
    fill: true,
  },
  pencil: {
    parts: [{ d: 'M12 20h9' }, { d: 'M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z' }],
  },
  play: {
    parts: [{ d: 'M7 5.5v13a1 1 0 0 0 1.5.87l11-6.5a1 1 0 0 0 0-1.74l-11-6.5A1 1 0 0 0 7 5.5z' }],
    fill: true,
  },
  plus: { parts: [{ d: 'M12 5v14M5 12h14' }] },
  refresh: { parts: [{ d: 'M21 12a9 9 0 1 1-6.2-8.6' }, { d: 'M21 3v6h-6' }] },
  'shield-check': {
    parts: [
      {
        d: 'M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z',
      },
      { d: 'm9 12 2 2 4-4' },
    ],
  },
  // Also two spellings of one glyph: the lid drawn first in five files, last in two.
  trash: {
    parts: [
      { d: 'M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6' },
    ],
  },
  'upload-cloud': {
    parts: [
      { d: 'M16 16l-4-4-4 4M12 12v9' },
      { d: 'M20.39 18.39A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3' },
    ],
  },
  video: {
    parts: [{ x: 3, y: 4, width: 14, height: 16, rx: 3 }, { d: 'm17 9 4-2v10l-4-2' }],
  },
  'x-circle': { parts: [{ cx: 12, cy: 12, r: 10 }, { d: 'm15 9-6 6M9 9l6 6' }] },
} satisfies Record<string, IconDef>;

export type IconName = keyof typeof ICONS;
