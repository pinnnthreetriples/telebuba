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
const merge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [{ text: ['micro', 'tiny', 'body', 'lead', 'title', 'stat', 'display', 'hero'] }],
      rounded: [{ rounded: ['card'] }],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return merge(clsx(inputs));
}
