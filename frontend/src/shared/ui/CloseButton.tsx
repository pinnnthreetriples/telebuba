import type { ButtonHTMLAttributes } from 'react';

import { Icon } from './Icon';
import { IconButton } from './IconButton';

export function CloseButton({
  size = 'md',
  className,
  ...rest
}: {
  size?: 'sm' | 'md' | 'lg' | 'touch';
  className?: string;
  'aria-label': string;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className' | 'children'>) {
  return (
    <IconButton {...rest} size={size} shape="circle" className={className}>
      <Icon name="close" size={16} />
    </IconButton>
  );
}
