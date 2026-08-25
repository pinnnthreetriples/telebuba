import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

// The shadcn/ui class-merge helper: clsx for conditional joins, tailwind-merge to
// dedupe conflicting Tailwind utilities (last one wins).
//
// tailwind-merge carries Tailwind's DEFAULT scales, and this config replaces two of
// them outright, so it has to be told the new names. Without that it cannot tell a
// type rung from a text colour — both are spelled `text-*` — and resolves
// `text-lead text-white` to `text-white`, silently dropping the size. That is not
// hypothetical: it is what `Button` produced, since its variant paints the colour
// after its size sets the rung.
//
// `font-size` and `border-radius` are the two groups the config replaces with names
// tailwind-merge has never seen. The other replaced scales (gap, duration, z-index,
// shadow) share their prefix with nothing else, so an unknown value there conflicts
// with the right group by prefix alone.
// The `type-*` roles are a third group, and they cannot join `font-size`: a role sets a
// size, a weight AND a colour, so folding it into the size group would let a later
// `text-lead` delete all three and leave the text unweighted and unpainted — the same
// shape of bug as the one above, one layer up. It gets its own group instead, declared
// to beat the three groups it subsumes when it comes last, and NOT declared as
// something they beat: `cn('type-caption', 'text-danger')` has to keep both, because
// naming the role and then recolouring it is the intended way to write an error line.
const merge = extendTailwindMerge<'type-role'>({
  extend: {
    classGroups: {
      'font-size': [
        { text: ['micro', 'tiny', 'body', 'lead', 'title', 'stat', 'display', 'hero'] },
      ],
      rounded: [{ rounded: ['card'] }],
      'type-role': [
        {
          type: [
            'page-title',
            'dialog-title',
            'dialog-body',
            'card-title',
            'item-title',
            'eyebrow',
            'label',
            'value',
            'prose',
            'caption',
            'meta',
            'stat',
          ],
        },
      ],
    },
    conflictingClassGroups: {
      'type-role': ['font-size', 'font-weight', 'text-color'],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return merge(clsx(inputs));
}
