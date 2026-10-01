import type { HTMLAttributes, ReactNode } from 'react';

import { settingHint, settingRow } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

export function SettingRow({
  label,
  labelTone = 'default',
  hint,
  first = false,
  htmlFor,
  children,
  className,
  ...rest
}: Omit<HTMLAttributes<HTMLDivElement>, 'children'> & {
  label: ReactNode;
  labelTone?: 'default' | 'body';
  hint?: ReactNode;
  first?: boolean;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn(settingRow(first), className)} {...rest}>
      <div className="min-w-0 flex-1">
        {htmlFor === undefined ? (
          <span className={labelTone === 'body' ? 'text-body' : 'type-label'}>{label}</span>
        ) : (
          <label htmlFor={htmlFor} className={labelTone === 'body' ? 'text-body' : 'type-label'}>
            {label}
          </label>
        )}
        {hint === undefined ? null : <div className={settingHint()}>{hint}</div>}
      </div>
      {children}
    </div>
  );
}
