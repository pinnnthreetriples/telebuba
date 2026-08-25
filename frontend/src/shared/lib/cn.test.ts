import { expect, test } from 'vitest';

import { cn } from './cn';

// The config replaces Tailwind's font-size scale outright, so tailwind-merge has to
// be told the new rung names. Untaught, it reads `text-lead` as a colour — both are
// spelled `text-*` — and drops it in favour of the colour that follows, which is
// exactly the order a variant component paints in.
test('a type rung survives the colour painted after it', () => {
  expect(cn('text-lead', 'text-white')).toBe('text-lead text-white');
  expect(cn('bg-canvas text-ink-muted', 'text-micro')).toBe('bg-canvas text-ink-muted text-micro');
});

test('two type rungs still collapse to the last one', () => {
  expect(cn('text-lead', 'text-body')).toBe('text-body');
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
// reads `type-caption` as an unknown class and keeps it next to the `text-lead` it was
// meant to replace — two sizes on one element, last-one-in-the-stylesheet wins.
test('a role replaces the rung, weight and colour written before it', () => {
  expect(cn('text-lead font-semibold text-ink-muted', 'type-caption')).toBe('type-caption');
  expect(cn('text-body', 'type-prose')).toBe('type-prose');
});

test('a colour after a role recolours it instead of replacing it', () => {
  expect(cn('type-caption', 'text-danger')).toBe('type-caption text-danger');
  expect(cn('type-card-title', 'font-bold')).toBe('type-card-title font-bold');
});

test('two roles still collapse to the last one', () => {
  expect(cn('type-caption', 'type-meta')).toBe('type-meta');
});
