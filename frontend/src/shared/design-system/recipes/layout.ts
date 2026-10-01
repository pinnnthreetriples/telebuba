import { layoutSettings as settings } from '../tokens/layout';
import type { PageFrameVariant, SectionGap } from '../tokens/layout';
import { GAP, MAX_WIDTH, PX, PY, MB, SPACE_Y } from './settings';
import { cn } from '@/shared/lib/cn';

export function pageFrame(variant: PageFrameVariant = 'page'): string {
  if (variant === 'app') {
    return 'layout-app';
  }
  if (variant === 'auth') return 'layout-auth';
  if (variant === 'settings')
    return cn(MAX_WIDTH[settings.settings.width], PX[settings.settings.x], PY[settings.settings.y]);
  return cn(
    'mx-auto w-full',
    MAX_WIDTH[settings[variant].width],
    PX[settings[variant].x],
    PY[settings[variant].y],
  );
}
export function boardLayout(variant: 'default' | 'launch' = 'default'): string {
  return variant === 'launch' ? 'layout-launch-board' : 'layout-board';
}
export function pageTitleSpacing(variant: 'default' | 'auth' = 'default'): string {
  return MB[variant === 'auth' ? settings.auth.titleGap : settings.titleGap];
}

export function sectionStack(gap: SectionGap = 'default', flow: 'flex' | 'space' = 'flex'): string {
  return flow === 'space'
    ? SPACE_Y[settings.sectionGap[gap]]
    : cn('flex flex-col', GAP[settings.sectionGap[gap]]);
}
