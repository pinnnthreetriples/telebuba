import type { PaddingSettings, RhythmToken } from '../tokens/components';
import { cn } from '@/shared/lib/cn';

// Literal maps are the Tailwind scanning boundary for token-backed settings.
export const P = {
  '0': 'p-0',
  px: 'p-px',
  hair: 'p-hair',
  xs: 'p-xs',
  tight: 'p-tight',
  sm: 'p-sm',
  md: 'p-md',
  lg: 'p-lg',
  xl: 'p-xl',
  '2xl': 'p-2xl',
  page: 'p-page',
  empty: 'p-empty',
} satisfies Record<RhythmToken, string>;

export const PX = {
  '0': 'px-0',
  px: 'px-px',
  hair: 'px-hair',
  xs: 'px-xs',
  tight: 'px-tight',
  sm: 'px-sm',
  md: 'px-md',
  lg: 'px-lg',
  xl: 'px-xl',
  '2xl': 'px-2xl',
  page: 'px-page',
  empty: 'px-empty',
} satisfies Record<RhythmToken, string>;

export const PY = {
  '0': 'py-0',
  px: 'py-px',
  hair: 'py-hair',
  xs: 'py-xs',
  tight: 'py-tight',
  sm: 'py-sm',
  md: 'py-md',
  lg: 'py-lg',
  xl: 'py-xl',
  '2xl': 'py-2xl',
  page: 'py-page',
  empty: 'py-empty',
} satisfies Record<RhythmToken, string>;

export const PB = {
  '0': 'pb-0',
  px: 'pb-px',
  hair: 'pb-hair',
  xs: 'pb-xs',
  tight: 'pb-tight',
  sm: 'pb-sm',
  md: 'pb-md',
  lg: 'pb-lg',
  xl: 'pb-xl',
  '2xl': 'pb-2xl',
  page: 'pb-page',
  empty: 'pb-empty',
} satisfies Record<RhythmToken, string>;

export const PT = {
  '0': 'pt-0',
  px: 'pt-px',
  hair: 'pt-hair',
  xs: 'pt-xs',
  tight: 'pt-tight',
  sm: 'pt-sm',
  md: 'pt-md',
  lg: 'pt-lg',
  xl: 'pt-xl',
  '2xl': 'pt-2xl',
  page: 'pt-page',
  empty: 'pt-empty',
} satisfies Record<RhythmToken, string>;

export const PL = {
  '0': 'pl-0',
  px: 'pl-px',
  hair: 'pl-hair',
  xs: 'pl-xs',
  tight: 'pl-tight',
  sm: 'pl-sm',
  md: 'pl-md',
  lg: 'pl-lg',
  xl: 'pl-xl',
  '2xl': 'pl-2xl',
  page: 'pl-page',
  empty: 'pl-empty',
} satisfies Record<RhythmToken, string>;

export const PR = {
  '0': 'pr-0',
  px: 'pr-px',
  hair: 'pr-hair',
  xs: 'pr-xs',
  tight: 'pr-tight',
  sm: 'pr-sm',
  md: 'pr-md',
  lg: 'pr-lg',
  xl: 'pr-xl',
  '2xl': 'pr-2xl',
  page: 'pr-page',
  empty: 'pr-empty',
} satisfies Record<RhythmToken, string>;

export const MB = {
  '0': 'mb-0',
  px: 'mb-px',
  hair: 'mb-hair',
  xs: 'mb-xs',
  tight: 'mb-tight',
  sm: 'mb-sm',
  md: 'mb-md',
  lg: 'mb-lg',
  xl: 'mb-xl',
  '2xl': 'mb-2xl',
  page: 'mb-page',
  empty: 'mb-empty',
} satisfies Record<RhythmToken, string>;

export const MT = {
  '0': 'mt-0',
  px: 'mt-px',
  hair: 'mt-hair',
  xs: 'mt-xs',
  tight: 'mt-tight',
  sm: 'mt-sm',
  md: 'mt-md',
  lg: 'mt-lg',
  xl: 'mt-xl',
  '2xl': 'mt-2xl',
  page: 'mt-page',
  empty: 'mt-empty',
} satisfies Record<RhythmToken, string>;

export const GAP = {
  '0': 'gap-0',
  px: 'gap-px',
  hair: 'gap-hair',
  xs: 'gap-xs',
  tight: 'gap-tight',
  sm: 'gap-sm',
  md: 'gap-md',
  lg: 'gap-lg',
  xl: 'gap-xl',
  '2xl': 'gap-2xl',
  page: 'gap-page',
  empty: 'gap-empty',
} satisfies Record<RhythmToken, string>;

export const MX_NEG = {
  '0': '-mx-0',
  px: '-mx-px',
  hair: '-mx-hair',
  xs: '-mx-xs',
  tight: '-mx-tight',
  sm: '-mx-sm',
  md: '-mx-md',
  lg: '-mx-lg',
  xl: '-mx-xl',
  '2xl': '-mx-2xl',
  page: '-mx-page',
  empty: '-mx-empty',
} satisfies Record<RhythmToken, string>;

export const MB_NEG = {
  '0': '-mb-0',
  px: '-mb-px',
  hair: '-mb-hair',
  xs: '-mb-xs',
  tight: '-mb-tight',
  sm: '-mb-sm',
  md: '-mb-md',
  lg: '-mb-lg',
  xl: '-mb-xl',
  '2xl': '-mb-2xl',
  page: '-mb-page',
  empty: '-mb-empty',
} satisfies Record<RhythmToken, string>;

export const WIDE_PX = {
  '0': 'sm:px-0',
  px: 'sm:px-px',
  hair: 'sm:px-hair',
  xs: 'sm:px-xs',
  tight: 'sm:px-tight',
  sm: 'sm:px-sm',
  md: 'sm:px-md',
  lg: 'sm:px-lg',
  xl: 'sm:px-xl',
  '2xl': 'sm:px-2xl',
  page: 'sm:px-page',
  empty: 'sm:px-empty',
} satisfies Record<RhythmToken, string>;

export const HEIGHT = {
  shellBottom: 'h-shellBottom',
  authOffset: 'h-authOffset',
  px: 'h-px',
  full: 'h-full',
  rail: 'h-rail',
  meter: 'h-meter',
  flag: 'h-flag',
  badge: 'h-badge',
  tile: 'h-tile',
  bar: 'h-bar',
  compact: 'h-compact',
  field: 'h-field',
  control: 'h-control',
  touch: 'h-touch',
  header: 'h-header',
  profileDialog: 'h-profileDialog',
} as const;
export const WIDTH = {
  0: 'w-0',
  px: 'w-px',
  auto: 'w-auto',
  max: 'w-max',
  full: 'w-full',
  board: 'w-board',
  flag: 'w-flag',
  action: 'w-action',
  number: 'w-number',
  readout: 'w-readout',
  stamp: 'w-stamp',
  col: 'w-col',
  menu: 'w-menu',
  tip: 'w-tip',
  sidebar: 'w-sidebar',
  logAccount: 'w-logAccount',
  confirm: 'w-confirm',
  form: 'w-form',
  panel: 'w-panel',
  table: 'w-table',
};

export const SIZE = {
  tick: 'size-tick',
  dot: 'size-dot',
  node: 'size-node',
  spinner: 'size-spinner',
  glyph: 'size-glyph',
  chip: 'size-chip',
  icon: 'size-icon',
  tile: 'size-tile',
  thumbnail: 'size-thumbnail',
  touch: 'size-touch',
  face: 'size-face',
} as const;

export const RADIUS = {
  none: 'rounded-none',
  sm: 'rounded-sm',
  md: 'rounded-md',
  lg: 'rounded-lg',
  card: 'rounded-card',
  full: 'rounded-full',
} as const;

export const TEXT = {
  tiny: 'text-tiny',
  body: 'text-body',
  title: 'text-title',
  stat: 'text-stat',
  display: 'text-display',
  hero: 'text-hero',
} as const;

export const MAX_WIDTH = {
  settings: 'max-w-settings',
  auth: 'max-w-auth',
  full: 'max-w-full',
  name: 'max-w-name',
  page: 'max-w-page',
  shell: 'max-w-shell',
} as const;

export const SPACE_Y = {
  0: 'space-y-0',
  px: 'space-y-px',
  hair: 'space-y-hair',
  xs: 'space-y-xs',
  tight: 'space-y-tight',
  sm: 'space-y-sm',
  md: 'space-y-md',
  lg: 'space-y-lg',
  xl: 'space-y-xl',
  '2xl': 'space-y-2xl',
  page: 'space-y-page',
  empty: 'space-y-empty',
};

export const ROLE = {
  'page-title': 'type-page-title',
  'dialog-title': 'type-dialog-title',
  'dialog-body': 'type-dialog-body',
  'card-title': 'type-card-title',
  'compact-title': 'type-compact-title',
  'field-error': 'type-field-error',
  'item-title': 'type-item-title',
  eyebrow: 'type-eyebrow',
  label: 'type-label',
  value: 'type-value',
  prose: 'type-prose',
  caption: 'type-caption',
  'table-header': 'type-table-header',
  stat: 'type-stat',
} as const;

export function paddingClasses(padding: PaddingSettings): string {
  return cn(
    padding.all !== undefined && P[padding.all],
    padding.x !== undefined && PX[padding.x],
    padding.y !== undefined && PY[padding.y],
    padding.bottom !== undefined && PB[padding.bottom],
    padding.top !== undefined && PT[padding.top],
  );
}
