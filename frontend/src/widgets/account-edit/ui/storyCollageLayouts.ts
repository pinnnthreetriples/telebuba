// Client-side mirror of the backend's `_COLLAGE_TEMPLATES`
// (core/telegram_client/_story_image.py). Each cell is [x, y, w, h] in
// fractions of a 9:16 canvas. The FIRST layout for a count is that count's
// default. These MUST match the backend byte-for-byte — a mismatched id is
// rejected server-side with `story_collage_unknown_layout`.

export const MAX_COLLAGE_IMAGES = 6;
export const MIN_COLLAGE_IMAGES = 2;

/** A single cell rectangle: [x, y, width, height] in [0, 1] canvas fractions. */
export type CollageCell = readonly [number, number, number, number];

export type CollageLayout = {
  readonly id: string;
  readonly cells: readonly CollageCell[];
};

const THIRD = 1 / 3;

export const COLLAGE_LAYOUTS: Readonly<Record<number, readonly CollageLayout[]>> = {
  2: [
    {
      id: 'v2',
      cells: [
        [0, 0, 1, 0.5],
        [0, 0.5, 1, 0.5],
      ],
    },
    {
      id: 'h2',
      cells: [
        [0, 0, 0.5, 1],
        [0.5, 0, 0.5, 1],
      ],
    },
  ],
  3: [
    {
      id: 'v3',
      cells: [
        [0, 0, 1, THIRD],
        [0, THIRD, 1, THIRD],
        [0, 2 * THIRD, 1, THIRD],
      ],
    },
    {
      id: 'left1_right2',
      cells: [
        [0, 0, 0.5, 1],
        [0.5, 0, 0.5, 0.5],
        [0.5, 0.5, 0.5, 0.5],
      ],
    },
    {
      id: 'top1_bottom2',
      cells: [
        [0, 0, 1, 0.5],
        [0, 0.5, 0.5, 0.5],
        [0.5, 0.5, 0.5, 0.5],
      ],
    },
  ],
  4: [
    {
      id: 'grid2x2',
      cells: [
        [0, 0, 0.5, 0.5],
        [0.5, 0, 0.5, 0.5],
        [0, 0.5, 0.5, 0.5],
        [0.5, 0.5, 0.5, 0.5],
      ],
    },
    {
      id: 'v4',
      cells: [
        [0, 0, 1, 0.25],
        [0, 0.25, 1, 0.25],
        [0, 0.5, 1, 0.25],
        [0, 0.75, 1, 0.25],
      ],
    },
  ],
  5: [
    {
      id: 'top2_bottom3',
      cells: [
        [0, 0, 0.5, 0.5],
        [0.5, 0, 0.5, 0.5],
        [0, 0.5, THIRD, 0.5],
        [THIRD, 0.5, THIRD, 0.5],
        [2 * THIRD, 0.5, THIRD, 0.5],
      ],
    },
  ],
  6: [
    {
      id: 'grid2x3',
      cells: [
        [0, 0, 0.5, THIRD],
        [0.5, 0, 0.5, THIRD],
        [0, THIRD, 0.5, THIRD],
        [0.5, THIRD, 0.5, THIRD],
        [0, 2 * THIRD, 0.5, THIRD],
        [0.5, 2 * THIRD, 0.5, THIRD],
      ],
    },
  ],
};

/** Layouts available for a given image count (empty when unsupported). */
export function layoutsForCount(count: number): readonly CollageLayout[] {
  return COLLAGE_LAYOUTS[count] ?? [];
}

/** The default layout id for a count (the first template), or null if none. */
export function defaultLayoutId(count: number): string | null {
  return layoutsForCount(count)[0]?.id ?? null;
}

/** Whether `id` is a valid layout for `count`. */
export function isLayoutValidForCount(id: string | null, count: number): boolean {
  return id !== null && layoutsForCount(count).some((layout) => layout.id === id);
}
