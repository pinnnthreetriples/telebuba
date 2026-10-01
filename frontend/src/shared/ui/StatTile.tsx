import type { HTMLAttributes, ReactNode } from 'react';

import { statTile, statTileLabel, type StatVariant } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

const TONE = {
  default: 'text-content-primary',
  muted: 'text-content-muted',
  info: 'text-info-strong',
  action: 'text-action-primary',
  success: 'text-success-deep',
  warning: 'text-warning-deep',
  danger: 'text-danger',
} as const;

export function StatTile({
  value,
  label,
  tone = 'default',
  variant = 'boxed',
  valuePresentation = 'default',
  className,
  ...rest
}: Omit<HTMLAttributes<HTMLDivElement>, 'children'> & {
  value: ReactNode;
  label: ReactNode;
  tone?: keyof typeof TONE;
  variant?: StatVariant;
  valuePresentation?: 'default' | 'custom';
}) {
  return (
    <div className={cn(statTile(variant), className)} {...rest}>
      {valuePresentation === 'custom' ? (
        value
      ) : (
        <div className={cn('type-stat', variant === 'launch' && 'tabular-nums', TONE[tone])}>
          {value}
        </div>
      )}
      <div className={statTileLabel(variant)}>{label}</div>
    </div>
  );
}
