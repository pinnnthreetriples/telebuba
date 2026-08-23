import type { ButtonHTMLAttributes, ReactNode } from 'react';

// The bordered white chip that carries an icon or a single glyph, in the four
// sizes the design actually uses. Shape follows size and is not a separate knob:
// the small ones are squares (a 24px circle reads as a dot beside 13px text), 34
// is the one circle, and 44 goes back to a square with the `md` radius because a
// 44px circle is a coin, not a control.
const SIZE = {
  sm: 'h-6 w-6 rounded-sm',
  md: 'h-7 w-7 rounded-md',
  lg: 'h-[34px] w-[34px] rounded-full',
  touch: 'h-11 w-11 rounded-md',
} as const;

// What the button MEANS, painted as the hover it takes. `neutral` deliberately
// has no hover state: it is the close/step glyph the design leaves inert, and
// giving it one would make every modal header twitch on the way past.
const TONE = {
  neutral: 'text-ink-muted',
  primary: 'text-ink-subtle hover:border-primary-line hover:bg-primary-wash hover:text-primary',
  danger: 'text-ink-subtle hover:border-danger-line hover:bg-danger-tint hover:text-danger',
} as const;

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
      className={`inline-flex shrink-0 items-center justify-center border border-line bg-white transition-colors disabled:opacity-50 ${SIZE[size]} ${TONE[tone]} ${className}`}
    >
      {children}
    </button>
  );
}
