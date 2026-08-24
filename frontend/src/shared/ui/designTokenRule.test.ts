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
      { code: 'const f = "w-[34px] h-[6px] max-w-[240px]";', name: 'component dimensions' },
      { code: 'const g = "p-0 m-0 gap-px";', name: 'zero and the hairline are not steps' },
      // Not utility classes at all: the pattern is anchored to a class boundary.
      { code: 'const h = "https://example.com/to-3/blue-500";', name: 'a url is not a class' },
    ],
    invalid: [
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
