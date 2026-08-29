import type { ButtonHTMLAttributes, ReactNode } from 'react';

import { FOCUS_RING } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

// The bordered white chip that carries an icon or a single glyph, in the four
// sizes the design actually uses.
//
// One utility per box rather than a `w`/`h` pair: this object used to write the
// same scale two ways in one literal — `h-11 w-11` beside `h-[34px] w-[34px]` —
// and a square whose two sides are separate decisions is a square that can stop
// being one.
const SIZE = {
  sm: 'size-chip',
  md: 'size-icon',
  lg: 'size-tile',
  touch: 'size-touch',
} as const;

// Форма — своя ось, а не следствие размера, и это правка.
//
// Следствием она была, с объяснением на каждую ступень: `sm` — `rounded-sm`, потому что
// «22px круг читается точкой рядом с 13px текстом»; `lg` — круг; `touch` — обратно квадрат,
// потому что «44px круг это монета, а не контрол». Три объяснения на четыре ступени — это
// не система, а три отдельных вкуса, и вместе они означали, что сменить размер иконочной
// кнопки нельзя, не сменив её форму.
//
// Квадрат один на все ступени, радиус — `md`: его носили 15 кнопок из 19. Круг остался, но
// он теперь ЗАПРОС, а не побочный эффект: `shape="circle"` стоит в одном месте — на
// корзине в модалке нейроаккаунтов, — и там это решение места вызова, которое видно в
// разметке.
const SHAPE = {
  square: 'rounded-md',
  circle: 'rounded-full',
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
  shape = 'square',
  tone = 'neutral',
  className = '',
  children,
  ...rest
}: {
  size?: keyof typeof SIZE;
  shape?: keyof typeof SHAPE;
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
        SHAPE[shape],
        TONE[tone],
        className,
      )}
    >
      {children}
    </button>
  );
}
