import postcss from 'postcss';
import tailwind from 'tailwindcss';
import { describe, expect, test } from 'vitest';

import config from '../../../tailwind.config';

// A transition that does not run is invisible to every other gate this repo has: it is
// not a raw value, not a contrast failure, not a drift between the config and the
// document, and not a type error. It shipped once — `transitionDuration` and
// `transitionTimingFunction` were moved to theme root to replace Tailwind's scales,
// which also dropped their `DEFAULT` keys, and Tailwind bakes those into every
// `transition-*` utility. `.transition-colors` came out carrying a `transition-property`
// and nothing else, CSS's initial `transition-duration` is 0s, and 25 of the app's
// transitions were switched off for a day with six gates green over them.
//
// So this file asserts the emitted CSS rather than the config object: the object was
// never wrong.
const durations = config.theme.transitionDuration as Record<string, string>;
const curves = config.theme.transitionTimingFunction as Record<string, string>;

const fixture = [
  'transition',
  'transition-colors',
  'transition-transform',
  ...Object.keys(durations).map((rung) => `duration-${rung}`),
  ...Object.keys(curves).map((curve) => `ease-${curve}`),
].join(' ');

const { css } = await postcss([
  tailwind({ ...config, content: [{ raw: fixture, extension: 'html' }] }),
]).process('@tailwind utilities;', { from: undefined });

function rule(selector: string): string {
  const at = css.indexOf(`${selector} {`);
  if (at === -1) throw new Error(`Tailwind emitted no rule for ${selector}`);
  return css.slice(at, css.indexOf('}', at));
}

describe('a bare transition utility carries a duration and a curve', () => {
  // The three the app actually writes without a `duration-*` beside them.
  for (const utility of ['.transition', '.transition-colors', '.transition-transform']) {
    test(utility, () => {
      const emitted = rule(utility);
      expect(emitted).toContain('transition-duration');
      expect(emitted).toContain('transition-timing-function');
      // Not merely present: a `0s` default would satisfy the two lines above and still
      // be the bug.
      expect(emitted).not.toMatch(/transition-duration:\s*0m?s/);
    });
  }
});

// The rungs themselves, so a renamed or deleted one fails here rather than silently
// resolving to nothing at the call sites that name it.
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
// them to a rung the config names rather than to a literal, so retuning `state`
// retunes the default with it instead of quietly splitting them apart.
test('the defaults are the rungs, not a second opinion', () => {
  expect(durations.DEFAULT).toBe(durations.state);
  expect(curves.DEFAULT).toBe(curves.out);
});
