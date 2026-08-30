import type { ButtonHTMLAttributes, ReactNode } from 'react';

import { FOCUS_RING } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

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
  // ЗАЛИТЫЙ, а не окрашенный по наведению: единственная иконочная кнопка, которая сама
  // является главным действием своего блока — генерация сценария. Остальные три тона
  // говорят, что случится при нажатии, и рисуются только наведением; эта говорит, что
  // нажать нужно именно её, и обязана быть видна до наведения. Краска взята у
  // `Button variant="primary"` дословно, чтобы залитая иконка и залитая кнопка не
  // разошлись.
  action: 'bg-action-primary text-on-action hover:bg-action-pressed',
  primary:
    'text-content-subtle hover:border-info-line hover:bg-action-hover hover:text-info-strong',
  danger:
    'text-content-subtle hover:border-danger-line hover:bg-danger-tint hover:text-danger-deep',
} as const;

// The same outline ring `Button` wears, and now literally the same string: `FOCUS_RING`
// comes from the control recipe instead of being spelled out here. This component never
// set a focus style at all, so it kept the browser's — which was legible, but differed per
// browser and per platform, and was the only control in the library not drawing its own.
//
// `className` is MERGED through `cn`, not appended. It used to be appended, under a note
// saying tailwind-merge would pull `shared/ui → shared/lib → the query barrel` — and that
// was true only of the barrel: `@/shared/lib/cn` is a leaf module, which `Card` and
// `FormField` had already been importing directly. Appending is not a smaller version of
// merging, it is a different outcome: two conflicting utilities both survive into the
// class list and the winner is decided by the order Tailwind happens to emit them in, not
// by the caller. A caller's override losing to the base is invisible until it matters.
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
      className={cn(
        'inline-flex shrink-0 items-center justify-center border border-line bg-surface-card transition-colors disabled:opacity-50',
        FOCUS_RING,
        SIZE[size],
        TONE[tone],
        className,
      )}
    >
      {children}
    </button>
  );
}
