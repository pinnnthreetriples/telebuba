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
// The focus ring is an OUTLINE, not `shadow-focus`. It was the shadow, and the shadow
// is `rgba(0,102,255,0.12)` — composited on this button's own white that is #e0edff,
// **1.18:1**, against the 3:1 that WCAG 2.2 asks of a focus indicator. Worse, it came
// with `outline-none`, so the browser ring it replaced was gone too: every one of the
// app's remaining hand-written buttons, which style focus not at all, was easier to
// follow by keyboard than the design system's own. `docs/design-system.html` states the
// correct rule ("обводку не заменяют тенью") and the component had been contradicting it.
// `shadow-focus` keeps its job on the FIELDS, where it is a glow beside a border that
// goes `primary` — 4.83:1, carrying the indication on its own.
const BASE =
  'inline-flex shrink-0 items-center justify-center gap-tight whitespace-nowrap transition-colors duration-state focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:pointer-events-none disabled:opacity-50 aria-busy:cursor-progress';

// `md` is the dialog footer and the page-level action; `sm` the action inside a
// card, where `md` would set the card header's height; `xs` the one that sits in a
// table row beside a value, and the only rung that is not a pill — at 22px tall a
// full radius and a rectangle are the same shape anyway. `block` is the action that
// spans its form, standing as the last row under the fields it commits.
//
// `block` is the one rung whose width is its own: the other three are as wide as
// their label, and a caller that wants them wider says so. It is also the one rung
// that is not `inline-flex`, because `w-full` on an inline-level box still sits on a
// line and collects that line's leading underneath it — a few pixels of space under
// six buttons that nobody chose, and exactly the kind of difference this component
// exists to stop carrying. Its `rounded-lg` is the radius scale's own name for a
// panel nested in a card, which is the shape a full-width row has; a pill here would
// be a 200px stadium.
//
// There is no rung between `sm` and `xs`, though seven buttons asked for one — a
// pill at `px-lg py-sm text-tiny`, 27px tall against `sm`'s 29 and `xs`'s 25. Two
// pixels is drift, not a decision, and every one of the seven sits inside a card,
// which is the sentence `sm` already answers. They are on `sm` now.
const SIZE = {
  md: 'rounded-full px-2xl py-md text-lead font-semibold',
  sm: 'rounded-full px-xl py-sm text-body font-semibold',
  xs: 'rounded-md px-md py-tight text-body font-medium',
  block: 'flex w-full rounded-lg py-md text-lead font-medium',
} as const;

// `primary` is the one committing action on a screen and `secondary` everything
// beside it; `danger` is the committing action when that action destroys something
// (it is a tinted button, not a red one — the red is the label); `ghost` has no box
// until you point at it; `dashed` adds one more of whatever the list above it holds,
// drawn as the empty slot the new thing will fill.
//
// `dashed` is a fill and not a shape, which is why it is here rather than in `SIZE`:
// its three wearers are all `block`, but `block` is worn by three different fills, so
// the two do not travel together. There is a SECOND dashed button in the app — the
// muted inline one that opens a channel field (neurocomment's CampaignsCard, the
// warming page) — and it is deliberately not this variant: it is drawn in
// `line-strong` and `ink-muted` rather than in blue, so folding it in would need a
// rung whose purpose could not be said without an "or". Those two stay hand-written.
const VARIANT = {
  primary: 'bg-primary text-white hover:bg-primary-press',
  secondary: 'border border-line bg-white text-ink hover:border-line-strong',
  danger: 'border border-danger-line bg-danger-tint text-danger-deep hover:border-danger',
  ghost: 'text-ink-muted hover:bg-canvas hover:text-ink',
  dashed:
    'border border-dashed border-primary-line bg-white text-primary-deep hover:border-primary hover:bg-primary-tint',
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
