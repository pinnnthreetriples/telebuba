import type { ReactNode } from 'react';

import { cn } from '@/shared/lib/cn';

// The tinted pill that labels a row — a count, a state, a "N удалено". Forty of
// them were written by hand, and the tone was a pair of classes the site picked
// itself: `bg-danger-tint text-danger-deep`, `bg-canvas text-ink-muted`. That pairing is
// where the app's contrast went: at 10.5px, `danger` on `danger-tint` measures
// 4.34:1 and `ink-muted` on the neutral fill 4.10:1, both under the 4.5:1 floor. The tone
// now names both halves at once, and it names the `deep` rung for the text.
//
// `Notice` is the block form of the same idea (a paragraph on a tinted panel);
// this one is inline and never wraps.
const TONE = {
  neutral: 'bg-canvas text-ink-muted',
  primary: 'bg-primary-tint text-primary-deep',
  success: 'bg-success-tint text-success-deep',
  warning: 'bg-warning-tint text-warning-deep',
  danger: 'bg-danger-tint text-danger-deep',
} as const;

// The app's own xs/sm/md control scale, the one `Button` and `Input` already read
// top-down, so a size name means the same thing wherever it is written. The middle
// rung is the one this component was missing and the reason it could not express
// the app's commonest pill: all three status badges and eleven more written by hand
// sit at `text-tiny`, which the type scale itself calls a pill's label. It had no
// name of its own because the two rungs that happened to be written first took `sm`
// and `md` between them; the smallest is `xs`, which is what it always measured.
const SIZE = {
  md: 'px-md py-tight text-body',
  sm: 'px-md py-xs text-tiny',
  xs: 'px-sm py-hair text-micro',
} as const;

// 6px over the 5px also in use: four of the app's seven status dots are already
// this one, and beside an 11px label the smaller reads as a printing flaw. Its
// diameter is a component's dimension and not a rung of the spacing rhythm, which
// is why it is written out rather than taken from the scale.
const DOT = 'h-[6px] w-[6px] shrink-0 rounded-full bg-current';

export type BadgeTone = keyof typeof TONE;

export function Badge({
  tone = 'neutral',
  size = 'xs',
  dot = false,
  className,
  children,
  ...rest
}: {
  tone?: BadgeTone;
  size?: keyof typeof SIZE;
  // The leading dot, `bg-current` so it can never disagree with the label. A prop
  // rather than a span the caller passes in, because a caller writing that span
  // re-decides the diameter and the gap each time, and those two disagreeing across
  // the app is the drift this component exists to end.
  dot?: boolean;
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
      {dot ? <span className={DOT} /> : null}
      {children}
    </span>
  );
}
