import type { HTMLAttributes } from 'react';

import {
  pageFrame,
  sectionStack,
  type PageFrameVariant,
  type SectionGap,
  type StackPadding,
  stackPadding,
} from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

export function PageFrame({
  variant = 'page',
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement> & { variant?: PageFrameVariant }) {
  return <div className={cn(pageFrame(variant), className)} {...rest} />;
}

export function SectionStack({
  gap = 'default',
  padding = 'none',
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement> & { gap?: SectionGap; padding?: StackPadding }) {
  return <div className={cn(sectionStack(gap), stackPadding(padding), className)} {...rest} />;
}
