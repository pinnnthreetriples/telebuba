import type { ButtonHTMLAttributes, ReactNode } from 'react';

// The bordered white chip that carries an icon or a single glyph, in the four
// sizes the design actually uses. Shape follows size and is not a separate knob:
// the small ones are squares (a 22px circle reads as a dot beside 13px text),
// `tile` is the one circle, and `touch` goes back to a square with the `md` radius
// because a 44px circle is a coin, not a control.
//
// One utility per box rather than a `w`/`h` pair: this object used to write the
// same scale two ways in one literal — `h-11 w-11` beside `h-[34px] w-[34px]` —
// and a square whose two sides are separate decisions is a square that can stop
// being one.
const SIZE = {
  sm: 'size-chip rounded-sm',
  md: 'size-icon rounded-md',
  lg: 'size-tile rounded-full',
  touch: 'size-touch rounded-md',
} as const;

// What the button MEANS, painted as the hover it takes. `neutral` deliberately
// has no hover state: it is the close/step glyph the design leaves inert, and
// giving it one would make every modal header twitch on the way past.
const TONE = {
  neutral: 'text-content-muted',
  primary:
    'text-content-subtle hover:border-info-line hover:bg-action-hover hover:text-info-strong',
  danger:
    'text-content-subtle hover:border-danger-line hover:bg-danger-tint hover:text-danger-deep',
} as const;

// The same outline ring `Button` wears. This component never set a focus style at
// all, so it kept the browser's — which was legible, but differed per browser and per
// platform, and was the only control in the library not drawing its own.
//
// `className` is appended, not merged: there is no tailwind-merge here (it would
// pull shared/ui → shared/lib → the query barrel), so callers pass extras that do
// not collide with the base — glyph size, a nudge margin, a breakpoint's display —
// and reach for `size`/`tone` for anything the base already owns.
export function IconButton({
  size = 'md',
  tone = 'neutral',
  className = '',
  children,
  ...rest
}: {
  size?: keyof typeof SIZE;
  tone?: keyof typeof TONE;
  className?: string;
  children: ReactNode;
  // Accessible name — REQUIRED, not optional, for the reason Modal's `label` is:
  // the content is an icon or a bare glyph, so without this a screen reader
  // announces an unnamed button and the control is unreachable by name.
  'aria-label': string;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className' | 'children'>) {
  return (
    <button
      type="button"
      {...rest}
      className={`inline-flex shrink-0 items-center justify-center border border-line bg-surface-card transition-colors disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action-primary ${SIZE[size]} ${TONE[tone]} ${className}`}
    >
      {children}
    </button>
  );
}
