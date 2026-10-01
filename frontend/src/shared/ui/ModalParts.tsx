import type { HTMLAttributes } from 'react';

import {
  modalBody,
  modalFooter,
  modalHeader,
  type ModalBodyVariant,
  type ModalFooterVariant,
  type ModalHeaderVariant,
  type SectionGap,
  sectionStack,
} from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

export function ModalHeader({
  variant = 'default',
  contentGap = 'default',
  flow = 'row',
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement> & {
  variant?: ModalHeaderVariant;
  contentGap?: 'default' | 'compact' | 'roomy';
  flow?: 'row' | 'column';
}) {
  return <div className={cn(modalHeader(variant, contentGap, flow), className)} {...rest} />;
}

export function ModalBody({
  variant = 'default',
  gap = 'none',
  flow = 'flex',
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement> & {
  variant?: ModalBodyVariant;
  gap?: SectionGap | 'none';
  flow?: 'flex' | 'space';
}) {
  return (
    <div
      className={cn(modalBody(variant), gap !== 'none' && sectionStack(gap, flow), className)}
      {...rest}
    />
  );
}

export function ModalFooter({
  variant = 'default',
  contentGap = 'default',
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement> & {
  variant?: ModalFooterVariant;
  contentGap?: 'default' | 'roomy';
}) {
  return <div className={cn(modalFooter(variant, contentGap), className)} {...rest} />;
}
