import { componentSettings as settings } from '../tokens/components';
import type {
  BodyPadding,
  CardAppearance,
  CardPadding,
  HeaderPadding,
  ModalBodyVariant,
  ModalFooterVariant,
  ModalHeaderVariant,
  SearchVariant,
  StatVariant,
  StackPadding,
} from '../tokens/components';
import {
  GAP,
  HEIGHT,
  MB,
  MB_NEG,
  MT,
  MX_NEG,
  PX,
  PY,
  RADIUS,
  ROLE,
  SIZE,
  WIDTH,
  paddingClasses,
} from './settings';
import { cn } from '@/shared/lib/cn';

const APPEARANCE = {
  card: 'rounded-card border border-line bg-surface-card',
  canvas: 'rounded-card border border-line bg-canvas',
  warning: 'rounded-card border border-warning-line bg-warning-tint',
  embedded: '',
  softCard: 'rounded-lg border border-line bg-surface-card',
} satisfies Record<CardAppearance, string>;

export function cardSurface(appearance: CardAppearance = 'card'): string {
  return APPEARANCE[appearance];
}

export function cardPadding(padding: CardPadding = 'default'): string {
  return paddingClasses(settings.card.padding[padding]);
}
export function stackPadding(padding: StackPadding = 'none'): string {
  return paddingClasses(settings.stack.padding[padding]);
}

export function cardTitle(): string {
  return cn(MB[settings.card.titleGap], ROLE[settings.card.titleRole]);
}

export function cardSubtitle(): string {
  return cn(MB[settings.card.subtitleGap], 'type-prose');
}

export function collapsibleHeader(padding: HeaderPadding = 'default', divider = false): string {
  return cn(
    'flex items-center transition-colors duration-state hover:bg-action-hover',
    GAP[settings.collapsible.gap],
    paddingClasses(settings.collapsible.headerPadding[padding]),
    divider && 'border-b border-line-row',
  );
}

export function collapsibleBody(padding: BodyPadding = 'default'): string {
  return paddingClasses(settings.collapsible.bodyPadding[padding]);
}

export function collapsibleTrigger(): string {
  return cn(
    'flex min-w-0 flex-1 items-center text-left transition duration-state',
    GAP[settings.collapsible.gap],
  );
}

export function formLabel(): string {
  return cn('block type-label', MB[settings.form.labelGap]);
}

export function fieldErrorText(): string {
  return cn('block type-field-error', MT[settings.form.errorGap]);
}

export function tableCell(header = false): string {
  return cn(
    PX[settings.inset.rowX],
    PY[settings.table.cellY],
    header && 'text-left type-table-header',
  );
}

export function tableCard(): string {
  return cn(
    'tb-row overflow-hidden border-t border-line-row first:border-t-0',
    PX[settings.inset.rowX],
    PY[settings.table.cardY],
  );
}

export function tableCardHead(): string {
  return cn('flex items-center', GAP[settings.table.gap]);
}

export function tableCardTitle(): string {
  // Cell renderers own text roles: timestamps and business names need different emphasis.
  return 'min-w-0 flex-1';
}

export function tableCardActions(): string {
  return MT[settings.table.gap];
}

export function tableCardRow(): string {
  return cn(
    'flex items-baseline justify-between first:mt-0',
    MT[settings.table.gap],
    GAP[settings.table.gap],
  );
}

export function tableCardSubRow(): string {
  return cn(MX_NEG[settings.inset.rowX], MB_NEG[settings.table.cardY], MT[settings.table.gap]);
}

export function modalHeader(
  variant: ModalHeaderVariant = 'default',
  contentGap: 'default' | 'compact' | 'roomy' = 'default',
  flow: 'row' | 'column' = 'row',
): string {
  return cn(
    flow === 'column' ? 'flex flex-col items-stretch' : 'flex items-center',
    !['listener', 'inline'].includes(variant) && 'border-b border-line-row',
    GAP[
      contentGap === 'roomy'
        ? settings.modal.headerRoomyGap
        : contentGap === 'compact'
          ? settings.modal.headerGap.default
          : settings.modal.headerGap[variant]
    ],
    paddingClasses(settings.modal.headerPadding[variant]),
  );
}

export function modalBody(variant: ModalBodyVariant = 'default'): string {
  return paddingClasses(settings.modal.bodyPadding[variant]);
}

export function modalFooter(
  variant: ModalFooterVariant = 'default',
  contentGap: 'default' | 'roomy' = 'default',
): string {
  return cn(
    'flex justify-end',
    !['plain', 'listener'].includes(variant) && 'border-t border-line-row',
    GAP[contentGap === 'roomy' ? settings.modal.footerRoomyGap : settings.modal.footerGap],
    paddingClasses(settings.modal.footerPadding[variant]),
  );
}

export function dialogTitle(): string {
  return cn('type-dialog-title', MB[settings.modal.titleGap]);
}
export function dialogBody(): string {
  return cn('type-dialog-body', MB[settings.modal.bodyGap]);
}

export function settingRow(first = false): string {
  return cn(
    'flex min-h-touch flex-wrap items-center',
    PY[settings.settingRow.y],
    GAP[settings.settingRow.gap],
    !first && 'border-t border-line-row',
  );
}

export function settingHint(): string {
  return cn('type-caption', MT[settings.settingRow.hintGap]);
}

export function statTile(variant: StatVariant = 'boxed'): string {
  if (variant !== 'boxed')
    return cn(
      paddingClasses(settings.statTile.padding[variant]),
      variant === 'runtime' && 'bg-surface-card',
    );
  return cn(
    'min-w-col rounded-lg border border-line bg-surface-card',
    PX[settings.inset.rowX],
    PY[settings.statTile.y],
  );
}

export function statTileLabel(variant: StatVariant = 'boxed'): string {
  return cn(
    'type-caption',
    MT[variant === 'boxed' ? settings.statTile.labelGap : settings.statTile.labelGaps[variant]],
  );
}

export function searchInputShell(variant: SearchVariant = 'default'): string {
  return cn(
    'tb-time flex items-center overflow-hidden border border-line bg-surface-card',
    HEIGHT[settings.search.height[variant]],
    RADIUS[variant === 'header' ? 'full' : settings.controls.fieldRadius.md],
    GAP[settings.search.gap],
  );
}

export function searchInputField(): string {
  return cn(
    'h-full min-w-0 w-full border-none bg-surface-card py-0 text-body outline-none',
    PX[settings.search.padding],
  );
}

export function iconButtonSize(
  size: keyof typeof settings.iconButton.size | 'fieldAction',
): string {
  return size === 'fieldAction'
    ? cn(HEIGHT[settings.iconButton.fieldHeight], WIDTH[settings.iconButton.fieldWidth])
    : SIZE[settings.iconButton.size[size]];
}
const RESPONSIVE_SIZE = {
  tick: 'md:size-tick',
  dot: 'md:size-dot',
  node: 'md:size-node',
  spinner: 'md:size-spinner',
  glyph: 'md:size-glyph',
  chip: 'md:size-chip',
  icon: 'md:size-icon',
  tile: 'md:size-tile',
  thumbnail: 'md:size-thumbnail',
  touch: 'md:size-touch',
  face: 'md:size-face',
};
const SMALL_SIZE = {
  tick: 'sm:size-tick',
  dot: 'sm:size-dot',
  node: 'sm:size-node',
  spinner: 'sm:size-spinner',
  glyph: 'sm:size-glyph',
  chip: 'sm:size-chip',
  icon: 'sm:size-icon',
  tile: 'sm:size-tile',
  thumbnail: 'sm:size-thumbnail',
  touch: 'sm:size-touch',
  face: 'sm:size-face',
};
const LARGE_SIZE = {
  tick: 'lg:size-tick',
  dot: 'lg:size-dot',
  node: 'lg:size-node',
  spinner: 'lg:size-spinner',
  glyph: 'lg:size-glyph',
  chip: 'lg:size-chip',
  icon: 'lg:size-icon',
  tile: 'lg:size-tile',
  thumbnail: 'lg:size-thumbnail',
  touch: 'lg:size-touch',
  face: 'lg:size-face',
};
export type ResponsiveIconSize =
  | keyof typeof settings.iconButton.size
  | Partial<Record<'sm' | 'md' | 'lg', keyof typeof settings.iconButton.size>>;
export function iconButtonResponsiveSize(size: ResponsiveIconSize): string {
  if (typeof size === 'string') return RESPONSIVE_SIZE[settings.iconButton.size[size]];
  return cn(
    size.sm && SMALL_SIZE[settings.iconButton.size[size.sm]],
    size.md && RESPONSIVE_SIZE[settings.iconButton.size[size.md]],
    size.lg && LARGE_SIZE[settings.iconButton.size[size.lg]],
  );
}
export function iconButtonShape(shape: keyof typeof settings.iconButton.shape): string {
  return RADIUS[settings.iconButton.shape[shape]];
}
