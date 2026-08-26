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
//
// `leading` and `tracking` are a third case, and the reason they are listed is the
// opposite of the `text-*` one: tailwind-merge DOES know both prefixes, but it matches
// them against Tailwind's own names plus a length or an arbitrary value, and
// `leading-stack`, `leading-log` and `tracking-code` are none of those. An unrecognised
// class joins no group, so it conflicts with nothing and `cn('leading-log',
// 'leading-none')` keeps BOTH — the winner then decided by the order the two rules
// happen to sit in the stylesheet rather than by the caller's last word. That is the
// same shape as the bug that switched off every filled Button's font size, one axis
// over, and it fails silently in exactly the same way.
// The `type-*` roles are a third group, and they cannot join `font-size`: a role sets a
// size, a weight AND a colour, so folding it into the size group would let a later
// `text-lead` delete all three and leave the text unweighted and unpainted — the same
// shape of bug as the one above, one layer up. It gets its own group instead, declared
// to beat the three groups it subsumes when it comes last, and NOT declared as
// something they beat: `cn('type-caption', 'text-danger')` has to keep both, because
// naming the role and then recolouring it is the intended way to write an error line.
//
// The `override` below is the other half of that, and it is a conflict tailwind-merge
// is right about everywhere except here. Stock Tailwind's `fontSize` entries are
// [size, line-height] tuples, so `text-lg` really does set a line-height and really
// does have to clear the `leading-*` written before it. This config's rungs are bare
// strings for exactly the opposite reason — the note above `fontSize` says pairing a
// line-height into them would silently re-space 694 sites — so `text-*` here sets a
// size and nothing else, and letting it clear a line-height drops a class the caller
// asked for: `cn('leading-log', 'text-micro')` returned `text-micro` alone. That is
// the same silent-drop shape as the Button bug, arriving from the other direction, and
// it was reachable before this axis had names at all, because an arbitrary
// `leading-[1.5]` lands in the same group a named rung does.
// The rhythm is the same case as `leading`/`tracking`, and it is the widest one: this
// config replaces Tailwind's numeric `spacing` with names, and tailwind-merge validates
// a padding, margin or gap value with `isLength` — which `tight`, `md` and `2xl` are
// not. So they join no group, conflict with nothing, and `cn('py-tight', 'py-xs')` keeps
// BOTH. The winner is then whichever class name happens to sort later in the emitted
// stylesheet, in either caller order, which means a component's own padding can beat the
// override its caller passed. Every `cn`-based component that takes a `className` is
// affected, and it fails the way all of these fail: silently, looking right most of the
// time. Found while trying to hold a button's height with a padding override — the
// override would have been discarded.
const RHYTHM = ['0', 'px', 'hair', 'xs', 'tight', 'sm', 'md', 'lg', 'xl', '2xl', 'page', 'empty'];

const merge = extendTailwindMerge<'type-role'>({
  override: {
    conflictingClassGroups: { 'font-size': [] },
  },
  extend: {
    classGroups: {
      'font-size': [
        { text: ['micro', 'tiny', 'body', 'lead', 'title', 'stat', 'display', 'hero'] },
      ],
      rounded: [{ rounded: ['card'] }],
      leading: [{ leading: ['stack', 'log'] }],
      tracking: [{ tracking: ['code'] }],
      // One entry per utility tailwind-merge resolves separately: `p` conflicts with
      // `px`/`py` and each of those with its own two sides, and that whole lattice is
      // already declared in the stock config — it is only the VALUES it does not
      // recognise. Naming them here restores the lattice rather than rebuilding it.
      p: [{ p: RHYTHM }],
      px: [{ px: RHYTHM }],
      py: [{ py: RHYTHM }],
      pt: [{ pt: RHYTHM }],
      pr: [{ pr: RHYTHM }],
      pb: [{ pb: RHYTHM }],
      pl: [{ pl: RHYTHM }],
      m: [{ m: RHYTHM }],
      mx: [{ mx: RHYTHM }],
      my: [{ my: RHYTHM }],
      mt: [{ mt: RHYTHM }],
      mr: [{ mr: RHYTHM }],
      mb: [{ mb: RHYTHM }],
      ml: [{ ml: RHYTHM }],
      gap: [{ gap: RHYTHM }],
      'gap-x': [{ 'gap-x': RHYTHM }],
      'gap-y': [{ 'gap-y': RHYTHM }],
      'space-x': [{ 'space-x': RHYTHM }],
      'space-y': [{ 'space-y': RHYTHM }],
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
