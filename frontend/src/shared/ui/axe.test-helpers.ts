import * as axe from 'axe-core';
import { expect } from 'vitest';

// An axe pass over the markup a component actually renders, run inside the suite that
// already renders it. It is a floor, not an audit: it catches the mechanical mistakes
// — a control with no accessible name, an aria attribute pointing at an id that is not
// there, a required child role missing — and it catches them on the day they are
// written rather than on the day someone reads the file again.
//
// What it does NOT catch is worth writing down, because a gate whose limits are
// unstated gets trusted for things it never checked. Contrast is measured by
// ./contrast.test.ts. Focus order, arrow-key behaviour and anything that needs a real
// focus ring are behaviour, which axe does not execute.
//
// ── Why some rules are off ───────────────────────────────────────────────────────
// happy-dom is not a browser, and exactly two of its gaps decide which rules are
// worth switching on. Both were measured here, not assumed:
//
//   Layout is zero. getBoundingClientRect() returns zeroes and scrollHeight and
//   clientHeight are 0. But getClientRects() still returns one rect, and that is what
//   axe's visibility check reads — so elements count as rendered and every structural
//   rule fires normally. Only rules that need real geometry are blind.
//
//   Background colour is the empty string. getComputedStyle(el).backgroundColor
//   returns '' for any element without an inline background, so axe walks the
//   ancestry looking for something to measure against, finds nothing, and cannot
//   decide. Font size, font weight and foreground colour DO come back, which is why
//   p-as-heading — which compares a paragraph's weight and size against its
//   neighbour's — stays on and works.
//
// Everything axe enables by default stays enabled, including rules added in axe
// versions later than this one, so the floor rises on its own.
const RULES: axe.RuleObject = {
  // Cannot decide, ever: with no computed background there is nothing to measure the
  // ink against, so every text node in every component lands in `incomplete` — which
  // is neither a pass nor a failure and would never fail this assertion. Contrast is
  // gated for real by ./contrast.test.ts, which reads the palette out of
  // tailwind.config.ts and walks the class lists the source actually paints,
  // including the ink a `type-*` role carries and the fill on an ancestor.
  'color-contrast': { enabled: false },
  // The same missing background, but failing the other way: instead of `incomplete`
  // it reports a PASS. A link distinguished from the body copy around it by colour
  // alone, with no underline, is a violation in a browser and green here. A rule that
  // is on and always green is worse than one that is off.
  'link-in-text-block': { enabled: false },
  // Needs the overflow to be real. happy-dom reports scrollHeight 0 on a box that
  // scrolls, so the rule never finds one to check and is inapplicable on every tree.
  'scrollable-region-focusable': { enabled: false },
};

// axe's own summaries, joined into one string. Compared as a string rather than as an
// array because a failure has to name the rule, the element and the reason, and a diff
// of two arrays of objects truncates before it gets to any of them.
const report = (violations: axe.Result[]): string =>
  violations
    .map(
      (violation) =>
        `${violation.id}: ${violation.help}\n  ${violation.helpUrl}\n` +
        violation.nodes
          .map((node) => `  ${node.html}\n  ${node.failureSummary ?? ''}`)
          .join('\n\n'),
    )
    .join('\n\n');

/**
 * Assert axe finds nothing wrong with what `root` currently renders.
 *
 * Pass the `container` from `render()`, or — for a component that portals, like Modal
 * and Toaster — the portal's own root element. Not `document.body`: at the body the
 * page-level rules become applicable and `region` flags every component fragment for
 * not sitting inside a landmark, which is the page shell's decision, not a shared/ui
 * component's.
 *
 * Not under `vi.useFakeTimers()`. axe schedules its own work on setTimeout, so on a
 * frozen clock it never resolves and the test dies of its timeout rather than of
 * anything it meant to assert.
 */
export async function expectNoAxeViolations(root: Element): Promise<void> {
  // A pass over nothing is indistinguishable from a clean one: an empty or already
  // unmounted root leaves every rule inapplicable and the assertion below passes on
  // it. This is the line that tells the two apart. It asks whether anything rendered
  // rather than whether any rule applied — a component of plain spans (Badge, Card)
  // legitimately gives axe nothing to check, and that is not the failure being
  // guarded against.
  expect(root.childElementCount).toBeGreaterThan(0);

  const results = await axe.run(root, { elementRef: false, rules: RULES });
  expect(report(results.violations)).toBe('');
}
