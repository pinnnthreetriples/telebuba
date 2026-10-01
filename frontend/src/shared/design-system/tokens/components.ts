import type { radius } from './primitives';
import type { height, rhythm, size, width } from './spacing';
import type { fontSize, typeRole } from './typography';

export type RhythmToken = keyof typeof rhythm;
export type RadiusToken = keyof typeof radius;
export type HeightToken = keyof typeof height;
export type SizeToken = keyof typeof size;
export type WidthToken = keyof typeof width;
export type FontSizeToken = keyof typeof fontSize;
export type TypographyRole = keyof typeof typeRole;
export type ControlSize = 'xs' | 'sm' | 'md' | 'lg';
export type CardPadding = 'default' | 'compact' | 'none' | 'empty' | 'panel' | 'vertical';
export type CardAppearance = 'card' | 'canvas' | 'warning' | 'embedded' | 'softCard';
export type HeaderPadding = 'default' | 'compact' | 'roomy' | 'none' | 'panel';
export type BodyPadding = 'default' | 'roomy' | 'tight' | 'none' | 'inset';
export type ModalHeaderVariant =
  'default' | 'roomy' | 'compact' | 'listener' | 'message' | 'inline' | 'selection';
export type ModalBodyVariant =
  | 'default'
  | 'compact'
  | 'form'
  | 'profile'
  | 'flush'
  | 'slim'
  | 'inset'
  | 'tight'
  | 'rows'
  | 'history'
  | 'wizard'
  | 'editor';
export type ModalFooterVariant = 'default' | 'compact' | 'profile' | 'inset' | 'plain' | 'listener';
export type SearchVariant = 'default' | 'header';
export type FieldVariant =
  'standard' | 'readout' | 'compactNumber' | 'inlineCaption' | 'inline' | 'auth';
export type AreaVariant = 'standard' | 'prompt' | 'composer';
export type StatVariant = 'boxed' | 'plain' | 'runtime' | 'launch';
export type StackPadding = 'none' | 'compact' | 'field' | 'empty';
export type BadgeSize = 'xs' | 'sm' | 'md';
export type BadgeEmphasis = 'medium' | 'semibold' | 'bold';
export type SegmentedVariant = 'tray' | 'pill' | 'outline';
export type ModalSize = 'confirm' | 'form' | 'panel' | 'table';
export type ModalVariant = 'center' | 'drawer-left' | 'viewer';

export interface PaddingSettings {
  all?: RhythmToken;
  x?: RhythmToken;
  y?: RhythmToken;
  bottom?: RhythmToken;
  top?: RhythmToken;
}

// These settings name existing tokens; their values are owned by the base scales.
// Recipes resolve the names through literal maps so Tailwind can see every class.
export const componentSettings = {
  badge: {
    emphasis: { medium: 'medium', semibold: 'semibold', bold: 'bold' } as Record<
      BadgeEmphasis,
      BadgeEmphasis
    >,
    padding: {
      md: { x: 'md', y: 'tight' },
      sm: { x: 'md', y: 'xs' },
      xs: { x: 'sm', y: 'hair' },
    } as Record<BadgeSize, PaddingSettings>,
    text: { md: 'body', sm: 'tiny', xs: 'tiny' } as Record<BadgeSize, FontSizeToken>,
    gap: { default: 'tight', roomy: 'sm' } as Record<'default' | 'roomy', RhythmToken>,
    radius: 'full' as RadiusToken,
    dotSize: 'dot' as SizeToken,
  },
  notice: {
    captionWeight: 'medium' as BadgeEmphasis,
    padding: { default: { x: 'md', y: 'md' }, compact: { x: 'md', y: 'sm' } } as Record<
      'default' | 'compact',
      PaddingSettings
    >,
    radius: 'lg' as RadiusToken,
    text: { body: 'body', caption: 'tiny' } as Record<'body' | 'caption', FontSizeToken>,
    rowGap: 'md' as RhythmToken,
  },
  tabList: {
    weight: 'medium' as BadgeEmphasis,
    gap: 'xl' as RhythmToken,
    x: 'xl' as RhythmToken,
    optionY: 'lg' as RhythmToken,
    text: 'body' as FontSizeToken,
  },
  segmented: {
    weight: 'medium' as BadgeEmphasis,
    pillRadius: 'full' as RadiusToken,
    wrapPadding: { tray: { all: 'xs' }, pill: { all: 'xs' }, outline: {} } as Record<
      SegmentedVariant,
      PaddingSettings
    >,
    gap: { tray: 'tight', pill: 0, outline: 'sm' } as Record<SegmentedVariant, RhythmToken>,
    optionPadding: {
      tray: { y: 'sm' },
      pill: { x: 'lg', y: 'tight' },
      outline: { x: 'md', y: 'sm' },
    } as Record<SegmentedVariant, PaddingSettings>,
    optionRadius: { tray: 'sm', pill: 'full', outline: 'lg' } as Record<
      SegmentedVariant,
      RadiusToken
    >,
    text: 'body' as FontSizeToken,
  },
  select: {
    selectedWeight: 'medium' as BadgeEmphasis,
    gap: 'sm' as RhythmToken,
    panelPadding: 'xs' as RhythmToken,
    optionPadding: { x: 'md', y: 'sm' } as PaddingSettings,
    optionRadius: 'sm' as RadiusToken,
    text: 'body' as FontSizeToken,
  },
  selectableCard: {
    padding: 'lg' as RhythmToken,
    radius: 'lg' as RadiusToken,
    gap: 'md' as RhythmToken,
    titleGap: 'tight' as RhythmToken,
    actionGap: 'sm' as RhythmToken,
    titleRole: 'card-title' as TypographyRole,
    metaRole: 'caption' as TypographyRole,
  },
  switch: {
    height: 'compact' as HeightToken,
    radius: 'full' as RadiusToken,
    thumbSize: 'chip' as SizeToken,
  },
  helpHint: {
    weight: 'bold' as BadgeEmphasis,
    badgeSize: 'glyph' as SizeToken,
    radius: 'full' as RadiusToken,
    text: 'tiny' as FontSizeToken,
    bubbleWidth: 'tip' as WidthToken,
    bubblePadding: 'md' as RhythmToken,
    exampleGap: 'tight' as RhythmToken,
  },
  spinner: {
    size: { sm: 'spinner', md: 'chip', lg: 'tile' } as Record<'sm' | 'md' | 'lg', SizeToken>,
    radius: 'full' as RadiusToken,
  },
  inset: { rowX: 'lg' as RhythmToken },
  stack: {
    padding: {
      none: {},
      compact: { all: 'lg' },
      field: { x: 'md', y: 'lg' },
      empty: { x: 'xl', y: 'empty' },
    } satisfies Record<StackPadding, PaddingSettings>,
  },
  card: {
    padding: {
      default: { x: 'xl', y: 'xl' },
      compact: { all: 'lg' },
      none: {},
      empty: { x: 'lg', y: 'empty' },
      panel: { x: 'xl', y: 'lg' },
      vertical: { y: 'xl' },
    } satisfies Record<CardPadding, PaddingSettings>,
    titleRole: 'compact-title' as TypographyRole,
    titleGap: 'xs' as RhythmToken,
    subtitleGap: 'lg' as RhythmToken,
  },
  collapsible: {
    headerPadding: {
      default: { x: 'lg', y: 'lg' },
      compact: { x: 'lg', y: 'md' },
      roomy: { x: 'xl', y: 'xl' },
      none: { x: 0, y: 0 },
      panel: { x: 'xl', y: 'lg' },
    } satisfies Record<HeaderPadding, PaddingSettings>,
    bodyPadding: {
      default: { x: 'lg', bottom: 'lg' },
      roomy: { x: 'xl', bottom: 'xl' },
      tight: { x: 'xl', bottom: 'tight' },
      none: {},
      inset: { x: 0, bottom: 0, top: 'md' },
    } satisfies Record<BodyPadding, PaddingSettings>,
    gap: 'md' as RhythmToken,
  },
  controls: {
    height: { xs: 'compact', sm: 'field', md: 'control', lg: 'touch' } as Record<
      ControlSize,
      HeightToken
    >,
    text: { xs: 'body', sm: 'body', md: 'body', lg: 'body' } satisfies Record<
      ControlSize,
      FontSizeToken
    >,
    buttonPadding: { xs: 'md', sm: 'xl', md: '2xl', lg: '2xl' } satisfies Record<
      ControlSize,
      RhythmToken
    >,
    fieldPadding: { xs: 'md', sm: 'md', md: 'md', lg: 'lg' } satisfies Record<
      ControlSize,
      RhythmToken
    >,
    areaPadding: { xs: 'tight', sm: 'tight', md: 'sm', lg: 'sm' } satisfies Record<
      ControlSize,
      RhythmToken
    >,
    fieldRadius: { xs: 'md', sm: 'lg', md: 'lg', lg: 'lg' } satisfies Record<
      ControlSize,
      RadiusToken
    >,
    buttonRadius: 'full' as RadiusToken,
    buttonWeight: { xs: 'medium', sm: 'semibold', md: 'semibold', lg: 'semibold' } as Record<
      ControlSize,
      'medium' | 'semibold'
    >,
    gap: 'tight' as RhythmToken,
    loadingGap: 'sm' as RhythmToken,
    roomyGap: 'sm' as RhythmToken,
    iconInset: 'page' as RhythmToken,
    trailingIconInset: 'control' as HeightToken,
    squareRadius: 'lg' as RadiusToken,
    multiline: {
      x: 'md' as RhythmToken,
      y: 'sm' as RhythmToken,
      wideX: 'xl' as RhythmToken,
      wideHeight: 'field' as HeightToken,
      minHeight: 'touch' as HeightToken,
    },
    compactFooterX: 'md' as RhythmToken,
    fieldWidths: {
      number: 'number',
      readout: 'readout',
      menu: 'menu',
      action: 'action',
      full: 'full',
    } satisfies Record<'number' | 'readout' | 'menu' | 'action' | 'full', WidthToken>,
    fieldVariants: {
      readout: { x: 'md', y: 'tight' },
      compactNumber: { x: 'md', y: 'tight' },
      inlineCaption: { all: 0 },
      inline: { all: 0 },
      auth: { x: 'md', y: 'md' },
    } satisfies Record<Exclude<FieldVariant, 'standard'>, PaddingSettings>,
    variantRadii: {
      readout: 'md',
      compactNumber: 'md',
      inlineCaption: 'none',
      inline: 'none',
      auth: 'lg',
    } as Record<Exclude<FieldVariant, 'standard'>, RadiusToken>,
    areaVariants: { prompt: { x: 'lg', y: 'md' }, composer: { x: 'md', y: 'sm' } } satisfies Record<
      Exclude<AreaVariant, 'standard'>,
      PaddingSettings
    >,
    areaRadii: { prompt: 'lg', composer: 'lg' } as Record<
      Exclude<AreaVariant, 'standard'>,
      RadiusToken
    >,
    areaText: { prompt: 'body' as FontSizeToken, composer: 'prose' as TypographyRole },
    composerMinHeight: 'control' as 'control' | 'touch',
  },
  iconButton: {
    size: { sm: 'chip', md: 'icon', lg: 'tile', touch: 'touch' } satisfies Record<
      'sm' | 'md' | 'lg' | 'touch',
      SizeToken
    >,
    shape: { square: 'md', circle: 'full', field: 'lg' } satisfies Record<
      'square' | 'circle' | 'field',
      RadiusToken
    >,
    fieldHeight: 'control' as HeightToken,
    fieldWidth: 'action' as const,
  },
  form: { labelGap: 'tight' as RhythmToken, errorGap: 'tight' as RhythmToken },
  table: {
    cellY: 'md' as RhythmToken,
    cardY: 'lg' as RhythmToken,
    gap: 'md' as RhythmToken,
  },
  modal: {
    shell: {
      mobilePadding: 'lg' as RhythmToken,
      desktopPadding: 'xl' as RhythmToken,
      widths: { confirm: 'confirm', form: 'form', panel: 'panel', table: 'table' } as Record<
        ModalSize,
        WidthToken
      >,
    },
    headerPadding: {
      default: { x: '2xl', top: 'xl', bottom: 'lg' },
      roomy: { x: 'xl', y: 'xl' },
      compact: { x: 'lg', y: 'lg' },
      listener: { x: 'xl', top: 'xl', bottom: 'lg' },
      message: { x: 'xl', y: 'lg' },
      inline: {},
      selection: { x: 'xl', y: 'md' },
    } satisfies Record<ModalHeaderVariant, PaddingSettings>,
    bodyPadding: {
      default: { x: '2xl', y: 'xl' },
      compact: { all: 'lg' },
      form: { all: '2xl' },
      profile: { all: 'xl' },
      flush: {},
      slim: { x: '2xl', y: 'sm' },
      inset: { x: '2xl', top: 'sm', bottom: 'lg' },
      tight: { x: '2xl', top: 'xs', bottom: 'xs' },
      rows: { x: '2xl', y: 'lg' },
      history: { x: '2xl', top: 'md', bottom: 'lg' },
      wizard: { x: '2xl', top: '2xl', bottom: 'xl' },
      editor: { all: '2xl' },
    } satisfies Record<ModalBodyVariant, PaddingSettings>,
    footerPadding: {
      default: { x: '2xl', y: 'lg' },
      compact: { all: 'lg' },
      profile: { x: 'xl', y: 'lg' },
      inset: { x: '2xl', top: 'lg', bottom: 'xl' },
      plain: {},
      listener: { x: 'xl', bottom: 'xl' },
    } satisfies Record<ModalFooterVariant, PaddingSettings>,
    headerGap: {
      default: 'md',
      roomy: 'lg',
      compact: 'md',
      listener: 'md',
      message: 'md',
      inline: 0,
      selection: 'md',
    } satisfies Record<ModalHeaderVariant, RhythmToken>,
    footerGap: 'sm' as RhythmToken,
    headerRoomyGap: 'lg' as RhythmToken,
    footerRoomyGap: 'md' as RhythmToken,
    titleGap: 'sm' as RhythmToken,
    bodyGap: '2xl' as RhythmToken,
  },
  settingRow: { y: 'sm' as RhythmToken, gap: 'md' as RhythmToken, hintGap: 'hair' as RhythmToken },
  statTile: {
    y: 'md' as RhythmToken,
    labelGap: 'px' as RhythmToken,
    padding: {
      boxed: {},
      plain: {},
      runtime: { x: 'lg', y: 'lg' },
      launch: { x: 'md', y: 'md' },
    } satisfies Record<StatVariant, PaddingSettings>,
    labelGaps: { boxed: 'px', plain: 0, runtime: 'hair', launch: 'xs' } satisfies Record<
      StatVariant,
      RhythmToken
    >,
  },
  search: {
    padding: 'md' as RhythmToken,
    gap: 'sm' as RhythmToken,
    height: { default: 'control', header: 'tile' } satisfies Record<SearchVariant, HeightToken>,
  },
};
