import type { ReactNode } from 'react';

import {
  badgeGeometry,
  badgeDot,
  type BadgeTone,
  type BadgeSize,
  type BadgeEmphasis,
} from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

// The tinted pill that labels a row — a count, a state, a "N удалено". Forty of
// them were written by hand, and the tone was a pair of classes the site picked
// itself: `bg-danger-tint text-danger-deep`, `bg-canvas text-content-muted`. That pairing is
// where the app's contrast went: at 10.5px, `danger` on `danger-tint` measures
// 4.34:1 and `ink-muted` on the neutral fill 4.10:1, both under the 4.5:1 floor. The tone
// now names both halves at once, and it names the `deep` rung for the text.
//
// `Notice` is the block form of the same idea (a paragraph on a tinted panel);
// this one is inline and never wraps.
// Тон приходит из `recipes/feedback.ts` — того же набора, что у Notice. Пять тонов были
// набраны дважды, поэтому `neutral` оказался только здесь, а рамка — только у Notice, и
// никто не решал ни того, ни другого.

// The app's own xs/sm/md control scale, the one `Button` and `Input` already read
// top-down, so a size name means the same thing wherever it is written. The middle
// rung is the one this component was missing and the reason it could not express
// the app's commonest pill: all three status badges and eleven more written by hand
// sit at `text-tiny`, which the type scale itself calls a pill's label. It had no
// name of its own because the two rungs that happened to be written first took `sm`
// and `md` between them; the smallest is `xs`, which is what it always measured.

// 6px over the 5px also in use: four of the app's seven status dots are already
// this one, and beside an 11px label the smaller reads as a printing flaw. Its
// diameter is a component's dimension and not a rung of the spacing rhythm, which
// is why it is written out rather than taken from the scale.

// Имя набора приходит из рецепта, а не объявляется здесь псевдонимом: пока оно было
// `Tone` в рецепте и `BadgeTone` тут, у одного множества было два имени.
export type { BadgeTone };

export function Badge({
  tone = 'neutral',
  size = 'xs',
  dot = false,
  emphasis = 'medium',
  contentGap = 'default',
  appearance = 'default',
  bordered = false,
  className,
  children,
  ...rest
}: {
  tone?: BadgeTone;
  size?: BadgeSize;
  emphasis?: BadgeEmphasis;
  contentGap?: 'default' | 'roomy';
  appearance?: 'default' | 'channel';
  bordered?: boolean;
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
        badgeGeometry(size, emphasis, contentGap, appearance, bordered, tone),
        className,
      )}
      {...rest}
    >
      {dot ? <span className={badgeDot()} /> : null}
      {children}
    </span>
  );
}
