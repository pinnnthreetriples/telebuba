import { RuleTester } from 'eslint';
import { test } from 'vitest';

import plugin from '../../../eslint-rules/design-tokens.js';

// A gate that flags nothing on the tree it lands on looks identical to a gate that
// flags nothing at all, so the rule gets its own fixtures: one per pattern, and the
// exceptions asserted as valid so a later "tightening" has to break a test to remove
// them. The rule lives in eslint-rules/ rather than src/, which is why its test sits
// here — vitest only collects under src/, and RuleTester finds describe/it through
// the globals the runner already installs.
const ruleTester = new RuleTester();
const rule = plugin.rules['no-raw-values'];

test('the design-token rule is wired', () => {
  if (!rule) throw new Error('no-raw-values is missing from the plugin');
});

if (rule) {
  ruleTester.run('no-raw-values', rule, {
    valid: [
      // The named set, which is the whole point.
      'const a = "bg-primary text-white px-2xl py-md rounded-full text-lead";',
      'const b = "gap-md mb-lg border-line-row shadow-pop duration-state";',
      // The written exceptions.
      { code: 'const c = "bg-white text-white";', name: 'white needs no name' },
      { code: 'const d = "rounded-[2px] rounded-[3px]";', name: 'hairline radii' },
      { code: 'const e = "pb-[80px] mt-[96px] py-[50px]";', name: 'page breathing room' },
      {
        code: 'const f = "size-tile h-meter w-col max-w-name";',
        name: 'dimensions have their own named scale',
      },
      {
        code: 'const f2 = "w-[min(84vw,300px)] max-w-[90vw] h-[1.1em]";',
        name: 'a dimension relative to the viewport or the text is not a rung',
      },
      { code: 'const g = "p-0 m-0 gap-px";', name: 'zero and the hairline are not steps' },
      // Not utility classes at all: the pattern is anchored to a class boundary.
      { code: 'const h = "https://example.com/to-3/blue-500";', name: 'a url is not a class' },
    ],
    invalid: [
      // The quiet one: `border-line-input` still renders a border, in preflight's own
      // grey, so nothing on screen says the token is gone.
      {
        code: 'const z = "border border-line-input";',
        errors: [{ message: /collapsed into another one/ }],
      },
      {
        code: 'const y = "hover:bg-primary-wash";',
        errors: [{ message: /collapsed into another one/ }],
      },
      {
        code: 'const a = "bg-blue-500";',
        errors: [{ message: /palette/ }],
      },
      {
        code: 'const b = "text-[#0066ff]";',
        errors: [{ message: /colour written into a class/ }],
      },
      {
        code: 'const c = "text-[12.5px]";',
        errors: [{ message: /type scale is closed/ }],
      },
      {
        code: 'const d = "px-3 py-2";',
        errors: [{ message: /4px grid/ }],
      },
      {
        code: 'const e = "gap-[11px]";',
        errors: [{ message: /rung within 2px/ }],
      },
      {
        code: 'const f = "rounded-[9px]";',
        errors: [{ message: /Five radii/ }],
      },
      {
        code: 'const g = "duration-[420ms]";',
        errors: [{ message: /Four motion rungs/ }],
      },
      // The exemption this rule used to carry, now the pattern it enforces: while
      // `w-*`/`h-*` in pixels were allowed, 73 distinct dimensions grew beside the
      // rhythm's eleven rungs.
      {
        code: 'const i = "lg:w-[34px] max-w-[240px]";',
        errors: [{ message: /Dimensions are their own scale/ }],
      },
      // The dimensions a single component owns are exempt at their own call sites and
      // nowhere else: the exemption is an inline suppression per site, not a hole in the
      // pattern, so the same measurement written a second time is still an error.
      {
        code: 'const j = "w-[46px] h-[62px] max-h-[120px] min-w-[220px]";',
        errors: [{ message: /Dimensions are their own scale/ }],
      },
      // The place the drift actually hid: a class string hoisted out of the JSX. The
      // rule reports the first pattern that matches, so one hoisted constant is one
      // error however many ways it drifted.
      {
        code: 'const FIELD = `w-full px-3 text-[13px]`;',
        errors: [{ message: /type scale is closed/ }],
      },
    ],
  });
}
