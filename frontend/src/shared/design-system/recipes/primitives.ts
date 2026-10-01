import { cn } from '@/shared/lib/cn';
import {
  componentSettings as settings,
  type BadgeEmphasis,
  type BadgeSize,
  type ModalSize,
  type ModalVariant,
  type SegmentedVariant,
} from '../tokens/components';
import { FOCUS_RING, PRESS_FEEDBACK, fieldBase } from './controls';
import { badgeTone, noticeTone, type BadgeTone, type FeedbackTone } from './feedback';
import { surface } from './surfaces';
import {
  GAP,
  HEIGHT,
  MB,
  MT,
  P,
  PX,
  PY,
  RADIUS,
  ROLE,
  SIZE,
  TEXT,
  WIDTH,
  paddingClasses,
} from './settings';

const WEIGHT = { medium: 'font-medium', semibold: 'font-semibold', bold: 'font-bold' };
export function badgeGeometry(
  size: BadgeSize,
  emphasis: BadgeEmphasis,
  contentGap: 'default' | 'roomy',
  appearance: 'default' | 'channel',
  bordered: boolean,
  tone: BadgeTone,
): string {
  const config = settings.badge;
  return cn(
    'inline-flex shrink-0 items-center whitespace-nowrap',
    RADIUS[config.radius],
    GAP[config.gap[contentGap]],
    paddingClasses(config.padding[size]),
    TEXT[config.text[size]],
    WEIGHT[config.emphasis[emphasis]],
    appearance === 'channel' ? 'bg-canvas text-content-secondary' : badgeTone(tone),
    bordered && 'border border-line',
  );
}
export function badgeDot(): string {
  return cn(SIZE[settings.badge.dotSize], RADIUS[settings.badge.radius], 'shrink-0 bg-current');
}
export function noticeGeometry(
  padding: 'default' | 'compact',
  typography: 'body' | 'caption',
  contentGap: 'none' | 'row',
  tone: FeedbackTone,
  bordered: boolean,
): string {
  const config = settings.notice;
  return cn(
    RADIUS[config.radius],
    paddingClasses(config.padding[padding]),
    TEXT[config.text[typography]],
    typography === 'caption' && WEIGHT[config.captionWeight],
    contentGap === 'row' && GAP[config.rowGap],
    noticeTone(tone, bordered),
  );
}
export function tabListGeometry(): string {
  return cn(
    'tb-scroll flex overflow-x-auto border-b border-line-row',
    GAP[settings.tabList.gap],
    PX[settings.tabList.x],
  );
}
export function tabOptionGeometry(selected: boolean): string {
  return cn(
    'shrink-0 whitespace-nowrap border-b-2 transition-colors',
    WEIGHT[settings.tabList.weight],
    PY[settings.tabList.optionY],
    TEXT[settings.tabList.text],
    FOCUS_RING,
    selected
      ? 'border-action-primary text-content-primary'
      : 'border-transparent text-content-muted hover:border-info-line',
  );
}
const SEGMENT_WRAP = {
  tray: `flex ${surface('inset')}`,
  pill: 'inline-flex border border-line bg-surface-card',
  outline: 'flex',
};
const SEGMENT_OPTION = { tray: 'flex-1', pill: '', outline: 'flex-1 border' };
const SEGMENT_ON = {
  tray: 'bg-surface-card text-content-primary shadow-seg',
  pill: 'bg-action-primary text-on-action shadow-pill',
  outline: 'border-action-primary bg-info-tint text-info-strong',
};
const SEGMENT_OFF = {
  tray: 'text-content-muted',
  pill: 'text-content-muted',
  outline:
    'border-line bg-surface-card text-content-muted hover:border-line-strong hover:bg-surface',
};
export function segmentedWrap(variant: SegmentedVariant): string {
  return cn(
    SEGMENT_WRAP[variant],
    variant === 'pill' && RADIUS[settings.segmented.pillRadius],
    variant !== 'pill' && GAP[settings.segmented.gap[variant]],
    paddingClasses(settings.segmented.wrapPadding[variant]),
  );
}
export function segmentedOption(variant: SegmentedVariant, selected: boolean): string {
  const config = settings.segmented;
  return cn(
    'group relative transition-colors duration-state disabled:opacity-60',
    WEIGHT[config.weight],
    FOCUS_RING,
    SEGMENT_OPTION[variant],
    paddingClasses(config.optionPadding[variant]),
    RADIUS[config.optionRadius[variant]],
    TEXT[config.text],
    selected ? SEGMENT_ON[variant] : SEGMENT_OFF[variant],
  );
}
export function selectTrigger(): string {
  return cn(
    fieldBase({ size: 'md' }),
    'flex items-center justify-between text-left text-content-primary',
    GAP[settings.select.gap],
    'border-line hover:border-line-strong focus-visible:border-focus focus-visible:shadow-focus',
    'disabled:cursor-default disabled:border-line disabled:bg-surface disabled:text-content-subtle',
  );
}
export function selectOption(): string {
  const config = settings.select;
  return cn(
    'flex w-full items-center justify-between border-none text-left hover:bg-action-hover disabled:text-content-subtle',
    GAP[config.gap],
    RADIUS[config.optionRadius],
    paddingClasses(config.optionPadding),
    TEXT[config.text],
  );
}
export function selectPanel(): string {
  return cn(P[settings.select.panelPadding], surface('panel'));
}
export function selectEmpty(): string {
  return cn(
    paddingClasses(settings.select.optionPadding),
    TEXT[settings.select.text],
    'text-content-subtle',
  );
}
export function selectSelected(): string {
  return cn(WEIGHT[settings.select.selectedWeight], 'text-info-strong');
}
export function selectableCardShell(selected: boolean): string {
  return cn(
    'relative border',
    RADIUS[settings.selectableCard.radius],
    P[settings.selectableCard.padding],
    selected ? 'border-action-primary bg-info-tint' : 'border-line bg-surface-card',
  );
}
export function selectableCardTarget(): string {
  return cn('absolute inset-0 cursor-pointer', RADIUS[settings.selectableCard.radius], FOCUS_RING);
}
export function selectableCardContent(): string {
  return cn('pointer-events-none flex justify-between', GAP[settings.selectableCard.gap]);
}
export function selectableCardTitle(): string {
  return cn(
    'truncate',
    MB[settings.selectableCard.titleGap],
    ROLE[settings.selectableCard.titleRole],
  );
}
export function selectableCardMeta(): string {
  return cn('truncate', ROLE[settings.selectableCard.metaRole]);
}
export function selectableCardActions(): string {
  return cn('flex shrink-0 flex-col items-end', GAP[settings.selectableCard.actionGap]);
}
export function switchTrack(checked: boolean): string {
  return cn(
    'tb-sw relative shrink-0 transition-colors duration-state disabled:pointer-events-none disabled:opacity-50',
    HEIGHT[settings.switch.height],
    RADIUS[settings.switch.radius],
    PRESS_FEEDBACK,
    FOCUS_RING,
    checked
      ? 'bg-action-primary hover:bg-action-pressed'
      : 'bg-line-strong hover:bg-content-subtle',
  );
}
export function switchThumb(): string {
  return cn(
    'tb-sw-thumb absolute block bg-surface-card shadow-thumb transition-transform',
    SIZE[settings.switch.thumbSize],
    RADIUS[settings.switch.radius],
  );
}
export function helpBadge(): string {
  const config = settings.helpHint;
  return cn(
    'flex shrink-0 cursor-help items-center justify-center border border-line leading-none text-content-subtle transition-colors hover:border-action-primary hover:text-action-primary focus:outline-none focus-visible:border-focus focus-visible:text-focus',
    WEIGHT[config.weight],
    SIZE[config.badgeSize],
    RADIUS[config.radius],
    TEXT[config.text],
  );
}
export function hintBubble(): string {
  return cn(
    'pointer-events-none absolute left-1/2 z-pop hidden -translate-x-1/2 text-left text-content-muted group-hover:block group-focus-within:block',
    WIDTH[settings.helpHint.bubbleWidth],
    P[settings.helpHint.bubblePadding],
    TEXT[settings.helpHint.text],
    surface('panel'),
  );
}
export function hintExample(): string {
  return cn('block text-content-subtle', MT[settings.helpHint.exampleGap]);
}
export function spinnerGeometry(size: 'sm' | 'md' | 'lg'): string {
  return cn(
    'tb-spin inline-block shrink-0',
    SIZE[settings.spinner.size[size]],
    RADIUS[settings.spinner.radius],
  );
}
const WIDE_P = {
  0: 'sm:p-0',
  px: 'sm:p-px',
  hair: 'sm:p-hair',
  xs: 'sm:p-xs',
  tight: 'sm:p-tight',
  sm: 'sm:p-sm',
  md: 'sm:p-md',
  lg: 'sm:p-lg',
  xl: 'sm:p-xl',
  '2xl': 'sm:p-2xl',
  page: 'sm:p-page',
  empty: 'sm:p-empty',
};
export function modalOverlay(variant: ModalVariant): string {
  return cn(
    'fixed inset-0 z-dialog flex bg-veil tb-ovfade',
    variant === 'center'
      ? cn(
          'justify-center overflow-y-auto overscroll-contain',
          P[settings.modal.shell.mobilePadding],
          WIDE_P[settings.modal.shell.desktopPadding],
        )
      : variant === 'viewer'
        ? 'items-stretch justify-stretch bg-term'
        : 'items-stretch justify-start',
  );
}
export function modalCard(variant: ModalVariant, size: ModalSize): string {
  // The drawer cap follows the viewport and has a single owner; it is not a reusable scale rung.
  return cn(
    'max-w-full outline-none',
    variant === 'center'
      ? cn('m-auto tb-arrive', surface('dialog'), WIDTH[settings.modal.shell.widths[size]])
      : variant === 'viewer'
        ? 'relative flex h-full w-full items-center justify-center'
        : 'flex h-full w-[min(84vw,300px)] flex-col overflow-y-auto overscroll-contain bg-surface-card tb-drawerin',
  );
}
