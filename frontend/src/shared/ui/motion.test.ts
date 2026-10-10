import { describe, expect, test } from 'vitest';

import { duration, easing, pressScale } from '@/shared/design-system/tokens';

import { compileClasses, ruleBody } from './tailwind.test-helpers';

// A transition that does not run is invisible to every other gate this repo has: it is
// not a raw value, not a contrast failure, not a drift between the tokens and the
// document, and not a type error. It shipped once — `transitionDuration` and
// `transitionTimingFunction` were moved to theme root to replace Tailwind's scales,
// which also dropped their `DEFAULT` keys, and Tailwind bakes those into every
// `transition-*` utility. `.transition-colors` came out carrying a `transition-property`
// and nothing else, CSS's initial `transition-duration` is 0s, and 25 of the app's
// transitions were switched off for a day with six gates green over them.
//
// Tailwind 4 keeps the same trap under another name: the defaults are the theme variables
// `--default-transition-duration` and `--default-transition-timing-function`, and a theme
// that resets `--*` without redeclaring them emits `var(--tw-duration, )` — an empty
// fallback, which is 0s again.
//
// So this file asserts the emitted CSS rather than the token object: the object was
// never wrong.
const durations: Record<string, string> = duration;
const curves: Record<string, string> = easing;

const fixture = [
  'transition',
  'transition-colors',
  'transition-transform',
  ...Object.keys(durations).map((rung) => `duration-${rung}`),
  ...Object.keys(curves).map((curve) => `ease-${curve}`),
  'scale-press',
  'active:scale-press',
  'disabled:active:scale-rest',
  'aria-busy:active:scale-rest',
  'motion-reduce:active:scale-rest',
];

const css = await compileClasses(fixture);

const rule = (selector: string): string => ruleBody(css, selector);

describe('a bare transition utility carries a duration and a curve', () => {
  // The three the app actually writes without a `duration-*` beside them.
  for (const utility of ['.transition', '.transition-colors', '.transition-transform']) {
    test(utility, () => {
      const emitted = rule(utility);
      // The default rung itself, not merely a declaration: `var(--tw-duration, )` would
      // satisfy "a transition-duration is declared" and still be the bug.
      expect(emitted).toContain(`transition-duration: var(--tw-duration, ${durations.state})`);
      expect(emitted).toContain(`transition-timing-function: var(--tw-ease, ${curves.out})`);
      expect(emitted).not.toMatch(/transition-duration:\s*0m?s/);
    });
  }
});

// The rungs themselves, so a renamed or deleted one fails here rather than silently
// resolving to nothing at the call sites that name it.
test('press scale utilities emit and reduced motion returns controls to rest', () => {
  expect(pressScale).toEqual({ rest: '1', press: '0.96' });
  expect(rule('.scale-press')).toContain(`--tw-scale-x: ${pressScale.press}`);
  expect(css).toContain('prefers-reduced-motion: reduce');
  expect(css).toContain('scale-rest');
});

describe('every motion rung emits its own value', () => {
  for (const [rung, value] of Object.entries(durations)) {
    if (rung === 'DEFAULT') continue;
    test(`duration-${rung} is ${value}`, () => {
      expect(rule(`.duration-${rung}`)).toContain(`transition-duration: ${value}`);
    });
  }
  for (const [curve, value] of Object.entries(curves)) {
    if (curve === 'DEFAULT') continue;
    test(`ease-${curve}`, () => {
      expect(rule(`.ease-${curve}`)).toContain(`transition-timing-function: ${value}`);
    });
  }
});

// The DEFAULT keys are what the utilities above pick up, and their whole reason for
// existing is that they are easy to delete without anything appearing to break. Pin
// them to a rung the tokens name rather than to a literal, so retuning `state`
// retunes the default with it instead of quietly splitting them apart.
test('the defaults are the rungs, not a second opinion', () => {
  expect(durations.DEFAULT).toBe(durations.state);
  expect(curves.DEFAULT).toBe(curves.out);
});
