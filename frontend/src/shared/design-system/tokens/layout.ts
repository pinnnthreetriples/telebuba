import type { maxWidth, rhythm } from './spacing';

type RhythmToken = keyof typeof rhythm;
type MaxWidthToken = keyof typeof maxWidth;
export type PageFrameVariant = 'app' | 'page' | 'full' | 'settings' | 'auth';
export type SectionGap = 'default' | 'compact' | 'roomy';

export const layoutSettings = {
  app: {
    x: 'lg' as RhythmToken,
    wideX: '2xl' as RhythmToken,
    width: 'shell' as MaxWidthToken,
    top: '2xl' as RhythmToken,
    bottom: 'shellBottom' as const,
  },
  page: { x: '0' as RhythmToken, y: '0' as RhythmToken, width: 'page' as MaxWidthToken },
  full: { x: '0' as RhythmToken, y: '0' as RhythmToken, width: 'full' as MaxWidthToken },
  settings: { x: '0' as RhythmToken, y: '0' as RhythmToken, width: 'settings' as MaxWidthToken },
  auth: {
    x: 'page' as RhythmToken,
    y: 'page' as RhythmToken,
    width: 'auth' as MaxWidthToken,
    top: 'authOffset' as const,
    titleGap: '2xl' as RhythmToken,
  },
  board: { sidebar: 'board' as const, gap: 'lg' as RhythmToken },
  launchBoard: { sidebar: 'sidebar' as const, gap: 'lg' as RhythmToken },
  titleGap: 'xl' as RhythmToken,
  sectionGap: { default: 'lg', compact: 'md', roomy: '2xl' } as Record<SectionGap, RhythmToken>,
};
