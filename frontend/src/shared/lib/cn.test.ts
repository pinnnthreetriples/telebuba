import { expect, test } from 'vitest';

import { cn } from './cn';

// The config replaces Tailwind's font-size scale outright, so tailwind-merge has to
// be told the new rung names. Untaught, it reads `text-body` as a colour — both are
// spelled `text-*` — and drops it in favour of the colour that follows, which is
// exactly the order a variant component paints in.
test('a type rung survives the colour painted after it', () => {
  expect(cn('text-body', 'text-on-fill')).toBe('text-body text-on-fill');
  expect(cn('bg-canvas text-content-muted', 'text-small')).toBe(
    'bg-canvas text-content-muted text-small',
  );
});

test('two type rungs still collapse to the last one', () => {
  expect(cn('text-body', 'text-body')).toBe('text-body');
});

test('two colours still collapse to the last one', () => {
  expect(cn('text-content-primary', 'text-danger-deep')).toBe('text-danger-deep');
  expect(cn('bg-action-primary', 'bg-success')).toBe('bg-success');
});

test('the three radii share one group', () => {
  expect(cn('rounded-md', 'rounded-lg')).toBe('rounded-lg');
  expect(cn('rounded-lg', 'rounded-sm')).toBe('rounded-sm');
});

// A role sets a size, a weight and a colour at once, so it has to beat all three when
// it comes last and survive a colour that comes after it. Untaught, tailwind-merge
// reads `type-small` as an unknown class and keeps it next to the `text-body` it was
// meant to replace — two sizes on one element, last-one-in-the-stylesheet wins.
test('a role replaces the rung, weight and colour written before it', () => {
  expect(cn('text-body font-medium text-content-muted', 'type-small')).toBe('type-small');
  expect(cn('text-body', 'type-body text-content-subtle')).toBe('type-body text-content-subtle');
});

test('a colour after a role recolours it instead of replacing it', () => {
  expect(cn('type-small', 'text-danger')).toBe('type-small text-danger');
  expect(cn('type-h3', 'font-normal')).toBe('type-h3 font-normal');
});

test('two roles still collapse to the last one', () => {
  expect(cn('type-small', 'type-h3')).toBe('type-h3');
});

test('the two weights collapse to the last one', () => {
  expect(cn('font-medium', 'font-normal')).toBe('font-normal');
});

// The one named line-height and the one letter-spacing are not lengths and not
// arbitrary values, so tailwind-merge matches them against neither half of its own
// `leading` and `tracking` groups. Untaught, it files them under no group at all and
// keeps the loser beside the winner, and which one paints is decided by the order
// Tailwind emitted the two rules, not by the caller.
test('the named line-height collapses with the one written after it', () => {
  expect(cn('leading-[1.1em]', 'leading-none')).toBe('leading-none');
  expect(cn('leading-none', 'leading-[1.1em]')).toBe('leading-[1.1em]');
  // A step carries its own line-height, but it is a size first: `leading-none` after it
  // is the glyph override and has to survive.
  expect(cn('text-small', 'leading-none')).toBe('text-small leading-none');
});

test('the code letter-spacing collapses with the one written after it', () => {
  expect(cn('tracking-code', 'tracking-[0.04em]')).toBe('tracking-[0.04em]');
  expect(cn('tracking-[0.04em]', 'tracking-code')).toBe('tracking-code');
});

// A line-height is its own axis: it must not be swallowed by a rung or a role, the way
// the config's own note insists `leading-*` stays an independent decision.
test('a line-height survives a rung and a role', () => {
  expect(cn('', 'text-small')).toBe('text-small');
  expect(cn('type-small', '')).toBe('type-small');
});

// The rhythm, and the reason it is the widest case of the three: a component's own
// padding is written FIRST and the caller's override LAST, so a scale tailwind-merge
// cannot parse does not merely leave two classes on the element — it lets the component
// beat its own caller, decided by which name sorts later in the stylesheet. Before the
// rungs were named here, `cn('py-tight', 'py-xs')` returned both and rendered `py-tight`.
// The rungs are Firecrawl's numeric keys now, which stock tailwind-merge parses on its own;
// the cases stay so a future named rung cannot quietly reopen the hole.
describe('a caller overrides the rhythm a component wrote first', () => {
  for (const [base, override] of [
    ['py-1', 'py-2'],
    ['px-3', 'px-4'],
    ['p-4', 'p-6'],
    ['gap-2', 'gap-3'],
    ['mt-8', 'mt-16'],
    ['-mt-1', 'mt-2'],
  ] as const) {
    test(`${base} then ${override}`, () => {
      expect(cn(base, override)).toBe(override);
    });
  }
});

// The lattice stock tailwind-merge already declares, which naming the values restores
// rather than replaces: an axis clears the two sides it covers, and `p` clears all four.
test('the shorthand still beats the sides it covers', () => {
  expect(cn('pt-3', 'py-4')).toBe('py-4');
  expect(cn('px-3', 'py-3', 'p-4')).toBe('p-4');
  // ...and not the other way round: a side written after an axis survives it.
  expect(cn('py-4', 'pt-3')).toBe('py-4 pt-3');
});
