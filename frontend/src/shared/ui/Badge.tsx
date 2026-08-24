import type { ReactNode } from 'react';

import { cn } from '@/shared/lib/cn';

// The tinted pill that labels a row — a count, a state, a "N удалено". Forty of
// them were written by hand, and the tone was a pair of classes the site picked
// itself: `bg-danger-tint text-danger`, `bg-track text-ink-muted`. That pairing is
// where the app's contrast went: at 10.5px, `danger` on `danger-tint` measures
// 4.34:1 and `ink-muted` on `track` 4.10:1, both under the 4.5:1 floor. The tone
// now names both halves at once, and it names the `deep` rung for the text.
//
// `Notice` is the block form of the same idea (a paragraph on a tinted panel);
// this one is inline and never wraps.
const TONE = {
  neutral: 'bg-track text-ink-muted',
  primary: 'bg-primary-tint text-primary-deep',
  success: 'bg-success-tint text-success-deep',
  warning: 'bg-warning-tint text-warning-deep',
  danger: 'bg-danger-tint text-danger-deep',
} as const;

// `sm` is the chip that rides beside a value in a table row; `md` the standalone
// label in a card header.
const SIZE = {
  sm: 'px-sm py-hair text-micro',
  md: 'px-md py-tight text-body',
} as const;

export function Badge({
  tone = 'neutral',
  size = 'sm',
  className,
  children,
  ...rest
}: {
  tone?: keyof typeof TONE;
  size?: keyof typeof SIZE;
  className?: string;
  children?: ReactNode;
} & Omit<React.HTMLAttributes<HTMLSpanElement>, 'className'>) {
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center gap-tight whitespace-nowrap rounded-full font-medium',
        TONE[tone],
        SIZE[size],
        className,
      )}
      {...rest}
    >
      {children}
    </span>
  );
}
