import type { InputHTMLAttributes, HTMLAttributes, ReactNode, Ref } from 'react';

import { searchInputField, searchInputShell, type SearchVariant } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

import { Icon } from './Icon';
import { IconButton } from './IconButton';

type ClearAction =
  { onClear?: undefined; clearLabel?: undefined } | { onClear: () => void; clearLabel: string };

export function SearchInput({
  variant = 'default',
  leading,
  onClear,
  clearLabel,
  className,
  ref,
  value,
  disabled,
  containerProps,
  ...rest
}: Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> & {
  variant?: SearchVariant;
  leading?: ReactNode;
  ref?: Ref<HTMLInputElement>;
  containerProps?: Omit<HTMLAttributes<HTMLDivElement>, 'className' | 'children'>;
} & ClearAction) {
  return (
    <div {...containerProps} className={cn(searchInputShell(variant), className)}>
      {leading === undefined ? null : (
        <span className="ml-md flex shrink-0 items-center" aria-hidden="true">
          {leading}
        </span>
      )}
      <input ref={ref} className={searchInputField()} value={value} disabled={disabled} {...rest} />
      {onClear && value !== undefined && value !== '' ? (
        <IconButton
          size="sm"
          aria-label={clearLabel}
          onClick={onClear}
          disabled={disabled}
          className="mr-md"
        >
          <Icon name="close" size={14} />
        </IconButton>
      ) : null}
    </div>
  );
}
