import type { ButtonHTMLAttributes, ReactNode } from 'react';

import { cn } from '@/shared/lib/cn';

// The app's text buttons, as the three shapes the design actually has and the four
// fills it paints them with. Before this they were 98 hand-written class strings
// across 72 distinct spellings, and the differences were rarely intentional: the
// same "cancel" button was `disabled:opacity-50` in one dialog and
// `disabled:opacity-60` in the next, hover was set on one button in sixty-six, and
// `focus-visible` appeared three times in the whole app.
//
// `IconButton` stays its own component rather than a size here: it is square, it
// carries no text, and its accessible name comes from `aria-label` — a button
// whose label is mandatory is a different contract, not a variant.
const BASE =
  'inline-flex shrink-0 items-center justify-center gap-tight whitespace-nowrap transition-colors duration-state focus-visible:shadow-focus focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50 aria-busy:cursor-progress';

// `md` is the dialog footer and the page-level action; `sm` the action inside a
// card, where `md` would set the card header's height; `xs` the one that sits in a
// table row beside a value, and the only rung that is not a pill — at 22px tall a
// full radius and a rectangle are the same shape anyway.
const SIZE = {
  md: 'rounded-full px-2xl py-md text-lead font-semibold',
  sm: 'rounded-full px-xl py-sm text-body font-semibold',
  xs: 'rounded-md px-md py-tight text-body font-medium',
} as const;

// `primary` is the one committing action on a screen and `secondary` everything
// beside it; `danger` is the committing action when that action destroys something
// (it is a tinted button, not a red one — the red is the label); `ghost` has no box
// until you point at it.
const VARIANT = {
  primary: 'bg-primary text-white hover:bg-primary-press',
  secondary: 'border border-line-input bg-white text-ink hover:border-line-strong',
  danger: 'border border-danger-line bg-danger-tint text-danger-deep hover:border-danger',
  ghost: 'text-ink-muted hover:bg-canvas hover:text-ink',
} as const;

export function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  disabled = false,
  className,
  children,
  ...rest
}: {
  variant?: keyof typeof VARIANT;
  size?: keyof typeof SIZE;
  // A request is in flight. The button reports it with `aria-busy` and stops taking
  // clicks, and that is all: this app says "Сохраняю…" in the label while it waits,
  // and a spinner next to that sentence would be the same fact told twice. Kept as
  // its own prop rather than folded into `disabled` because a screen reader must
  // hear the difference between "busy" and "off".
  loading?: boolean;
  children?: ReactNode;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className'> & { className?: string }) {
  return (
    <button
      type="button"
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(BASE, SIZE[size], VARIANT[variant], className)}
      {...rest}
    >
      {children}
    </button>
  );
}
