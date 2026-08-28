import { expect, test } from 'vitest';

import { cn } from './cn';

// The config replaces Tailwind's font-size scale outright, so tailwind-merge has to
// be told the new rung names. Untaught, it reads `text-body` as a colour — both are
// spelled `text-*` — and drops it in favour of the colour that follows, which is
// exactly the order a variant component paints in.
test('a type rung survives the colour painted after it', () => {
  expect(cn('text-body', 'text-white')).toBe('text-body text-white');
  expect(cn('bg-canvas text-ink-muted', 'text-tiny')).toBe('bg-canvas text-ink-muted text-tiny');
});

test('two type rungs still collapse to the last one', () => {
  expect(cn('text-body', 'text-body')).toBe('text-body');
});

test('two colours still collapse to the last one', () => {
  expect(cn('text-ink', 'text-danger-deep')).toBe('text-danger-deep');
  expect(cn('bg-primary', 'bg-success')).toBe('bg-success');
});

test('the card radius belongs to the radius group', () => {
  expect(cn('rounded-lg', 'rounded-card')).toBe('rounded-card');
});

// A role sets a size, a weight and a colour at once, so it has to beat all three when
// it comes last and survive a colour that comes after it. Untaught, tailwind-merge
// reads `type-caption` as an unknown class and keeps it next to the `text-body` it was
// meant to replace — two sizes on one element, last-one-in-the-stylesheet wins.
test('a role replaces the rung, weight and colour written before it', () => {
  expect(cn('text-body font-semibold text-ink-muted', 'type-caption')).toBe('type-caption');
  expect(cn('text-body', 'type-prose')).toBe('type-prose');
});

test('a colour after a role recolours it instead of replacing it', () => {
  expect(cn('type-caption', 'text-danger')).toBe('type-caption text-danger');
  expect(cn('type-card-title', 'font-bold')).toBe('type-card-title font-bold');
});

test('two roles still collapse to the last one', () => {
  expect(cn('type-caption', 'type-caption')).toBe('type-caption');
});

// The named line-heights and the one letter-spacing are not lengths and not arbitrary
// values, so tailwind-merge matches them against neither half of its own `leading` and
// `tracking` groups. Untaught, it files them under no group at all and keeps the loser
// beside the winner: `cn('leading-log', 'leading-none')` returns both, and which one
// paints is decided by the order Tailwind emitted the two rules, not by the caller.
test('a named line-height collapses with the rung written after it', () => {
  expect(cn('leading-log', 'leading-none')).toBe('leading-none');
  expect(cn('leading-none', 'leading-stack')).toBe('leading-stack');
  expect(cn('text-tiny leading-stack', 'leading-log')).toBe('text-tiny leading-log');
});

test('the code letter-spacing collapses with the one written after it', () => {
  expect(cn('tracking-code', 'tracking-[0.04em]')).toBe('tracking-[0.04em]');
  expect(cn('tracking-[0.04em]', 'tracking-code')).toBe('tracking-code');
});

// A line-height is its own axis: it must not be swallowed by a rung or a role, the way
// the config's own note insists `leading-*` stays an independent decision.
test('a line-height survives a rung and a role', () => {
  expect(cn('leading-log', 'text-tiny')).toBe('leading-log text-tiny');
  expect(cn('type-caption', 'leading-stack')).toBe('type-caption leading-stack');
});

// The rhythm, and the reason it is the widest case of the three: a component's own
// padding is written FIRST and the caller's override LAST, so a scale tailwind-merge
// cannot parse does not merely leave two classes on the element — it lets the component
// beat its own caller, decided by which name sorts later in the stylesheet. Before the
// rungs were named here, `cn('py-tight', 'py-xs')` returned both and rendered `py-tight`.
describe('a caller overrides the rhythm a component wrote first', () => {
  for (const [base, override] of [
    ['py-tight', 'py-xs'],
    ['px-md', 'px-lg'],
    ['p-lg', 'p-2xl'],
    ['gap-sm', 'gap-md'],
    ['mt-page', 'mt-empty'],
  ] as const) {
    test(`${base} then ${override}`, () => {
      expect(cn(base, override)).toBe(override);
    });
  }
});

// The lattice stock tailwind-merge already declares, which naming the values restores
// rather than replaces: an axis clears the two sides it covers, and `p` clears all four.
test('the shorthand still beats the sides it covers', () => {
  expect(cn('pt-md', 'py-lg')).toBe('py-lg');
  expect(cn('px-md', 'py-md', 'p-lg')).toBe('p-lg');
  // ...and not the other way round: a side written after an axis survives it.
  expect(cn('py-lg', 'pt-md')).toBe('py-lg pt-md');
});
