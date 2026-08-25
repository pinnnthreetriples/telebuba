import postcss, { type AtRule, type Rule } from 'postcss';
import tailwind from 'tailwindcss';
import { describe, expect, test } from 'vitest';

import config from '../../../tailwind.config';

import source from './index.css?raw';

// index.css holds the parts Tailwind utilities cannot express, and two of them are
// invisible to every other gate this repo has: an animation that keeps moving under
// `prefers-reduced-motion` is not a raw value, not a contrast failure, not a drift
// between the config and the document, and not a type error — and a tooltip that only
// opens on `:hover` is a rule that is CORRECT for every pointer and missing for every
// keyboard. Neither can be reached from a component test, because happy-dom applies no
// stylesheet at all: the components can only be asked whether their side of the contract
// is there (a focusable trigger, an `aria-describedby` that resolves), and this file asks
// the stylesheet for the other side.
//
// It runs the real PostCSS/Tailwind pipeline over the file rather than reading its text,
// so a `theme()` call that stopped resolving fails here too. `?raw` rather than a path off
// `import.meta.url`, which under Vite is not a `file:` URL. The `@import`s at the top are
// left alone — nothing in this file resolves them, and nothing here asks about fonts.
const { root } = await postcss([
  tailwind({ ...config, content: [{ raw: '', extension: 'html' }] }),
]).process(source, {
  from: undefined,
});

function reducedMotionBlocks(): AtRule[] {
  const found: AtRule[] = [];
  root.walkAtRules('media', (at) => {
    if (at.params.includes('prefers-reduced-motion')) found.push(at);
  });
  return found;
}

describe('prefers-reduced-motion', () => {
  // One blanket block, not a list of class names. The list is what this used to be, and
  // it reached three of the app's twenty-odd moving things: it could never have reached
  // the eight `[animation:…]` arbitrary utilities written inline in five components,
  // because those classes are minted by Tailwind and named nowhere in this file.
  test('is one blanket block covering every element and pseudo-element', () => {
    const blocks = reducedMotionBlocks();
    expect(blocks).toHaveLength(1);

    const selectors = new Set<string>();
    blocks[0]?.walkRules((rule: Rule) => {
      for (const selector of rule.selectors) selectors.add(selector);
    });
    expect(selectors).toEqual(new Set(['*', '*::before', '*::after']));
  });

  // The whole reason this is a near-zero DURATION and not `animation: none` /
  // `transition: none`: two of the app's transitions carry logic on their completion
  // event, and a removed transition fires no event at all. CollapsibleCard keys
  // `.tb-settled` off the open's max-height transitionend and `hidden` off the close's
  // opacity one; DataTable unmounts a closing sub-row on its grid-template-rows
  // transitionend. Kill those and a collapsed card keeps its entire body in the tab
  // order and the a11y tree — which is a WORSE accessibility bug than the motion this
  // block exists to stop.
  test('stops the motion without stopping the completion events', () => {
    const declared = new Map<string, string>();
    reducedMotionBlocks()[0]?.walkDecls((decl) => {
      declared.set(decl.prop, decl.value);
      expect(decl.important).toBe(true);
    });

    expect(declared.get('animation-duration')).toBe('0.01ms');
    expect(declared.get('transition-duration')).toBe('0.01ms');
    // A near-zero duration repeated forever is still a flicker, so the infinite pulses
    // need the iteration count too.
    expect(declared.get('animation-iteration-count')).toBe('1');
    expect(declared.get('animation-delay')).toBe('0ms');
    expect(declared.get('transition-delay')).toBe('0ms');

    // The shorthands are the trap: `animation: none` and `transition: none` would satisfy
    // "the motion stopped" and take the two transitionend handlers with them.
    expect(declared.has('animation')).toBe(false);
    expect(declared.has('transition')).toBe(false);
  });

  // `!important` on `*` is what beats a `transition:`/`animation:` SHORTHAND declared
  // later in the file — a media query adds no specificity, and a shorthand resets every
  // longhand a plain override set. `.tb-subrow` used to carry its own exemption BELOW its
  // base rule for exactly that reason; this is what replaced it.
  test('the blanket outranks every animation and transition shorthand in the file', () => {
    const shorthands: string[] = [];
    root.walkDecls((decl) => {
      if (decl.prop === 'animation' || decl.prop === 'transition') {
        // PostCSS leaves `important` undefined rather than false on a plain declaration.
        expect(decl.important ?? false).toBe(false);
        shorthands.push(decl.prop);
      }
    });
    // If this ever reads zero the assertion above stopped meaning anything.
    expect(shorthands.length).toBeGreaterThan(10);
  });
});

// The dark tooltip and the light one (`HelpHint`'s `HintBubble`) are two deliberate
// LOOKS — dark for a control's label, light for an explanation, per the canon. A look is
// not a reason for one of them to be pointer-only, and the light one has carried
// `group-focus-within:block` since it was written.
test('the dark tooltip opens on focus as well as hover', () => {
  const selectors: string[] = [];
  root.walkRules((rule: Rule) => {
    if (rule.selectors.some((selector) => selector.endsWith('.tb-tip-pop'))) {
      selectors.push(...rule.selectors);
    }
  });

  expect(selectors).toContain('.tb-tip:hover .tb-tip-pop');
  expect(selectors).toContain('.tb-tip:focus-within .tb-tip-pop');
});
