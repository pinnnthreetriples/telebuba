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
      'const a = "bg-action-primary text-on-action px-2xl py-md rounded-full text-body";',
      'const b = "gap-md mb-lg border-line-row shadow-pop duration-state";',
      // The written exceptions.
      {
        // Роль названа в самом классе: белая поверхность и надпись на залитом действии
        // больше не один `white`. Голый `white`/`black` теперь ошибка, и её случай — ниже.
        code: 'const c = "bg-surface-card text-on-action";',
        name: 'a white surface and ink on a filled action are two names now',
      },
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
      {
        code: 'const g2 = "bg-white/85 bg-black/10 border-white/40 border-black/5";',
        name: 'an alpha on white or black is a wash over content the palette cannot know',
      },
      {
        code: 'const g3 = "bg-info-tint text-info-strong bg-scrim bg-veil bg-surface";',
        name: 'the tokens the seven alpha sites turned out to be',
      },
      {
        code: "const g4 = 'linear-gradient(135deg,#cfd8ec,#e7dfd2)';",
        name: 'hex in a style value: the two decorative gradients keep their exemption',
      },
      // Not utility classes at all: the pattern is anchored to a class boundary.
      { code: 'const h = "https://example.com/to-3/blue-500";', name: 'a url is not a class' },
      // The type roles, and the four things the role pattern deliberately cannot reach.
      // RuleTester reports no filename, so these run as if they were above `shared/ui`.
      {
        code: 'const t1 = "mt-px type-caption"; const t2 = "type-caption text-danger";',
        name: 'a role, and a role recoloured',
      },
      {
        code: 'const t3 = "rounded-full border border-line bg-surface-card px-md py-xs text-tiny text-content-muted";',
        name: 'a class list that paints a box is drawing a control',
      },
      {
        code: 'const t3b = "px-lg py-empty text-center type-prose"; const t3c = "p-page type-prose text-content-primary";',
        name: 'a padded gap holding a role, with and without a colour override',
      },
      {
        code: 'const t4 = "text-body text-content-muted hover:text-action-primary";',
        name: 'a class list that reacts to the pointer is drawing a control',
      },
      {
        code: 'const t5 = "text-body leading-none text-content-subtle"; const t6 = "absolute left-lg text-body text-content-subtle";',
        name: 'at `lead` the scale doubles as a glyph size',
      },
      {
        code: 'const t7 = "min-w-badge text-body font-semibold";',
        name: "a weight with no grey is a number's emphasis, which no role can absorb",
      },
      // The two line-heights and the one letter-spacing that are left, and the two
      // things the line-height pattern deliberately cannot reach.
      {
        code: 'const u1 = "leading-stack leading-log tracking-code";',
        name: 'the named rungs, which is the whole point',
      },
      {
        code: 'const u2 = "text-body leading-none text-on-action";',
        name: 'a single glyph has no line-height, and `none` is the rung that says so',
      },
      {
        code: 'const u3 = "h-[1.1em] type-stat leading-[1.1em] tabular-nums";',
        name: "the odometer's line-height is measured against the text, like the box beside it",
      },
      {
        code: 'const u4 = "text-tiny font-medium uppercase tracking-[0.04em] text-content-subtle";',
        filename: 'src/shared/ui/DataTable.tsx',
        name: '`shared/ui` composes a column label by hand, letter-spacing included',
      },
      {
        code: 'const w = "text-title font-bold tracking-[-0.01em]";',
        name: "the wordmark keeps the design source's own spacing",
      },
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
        code: 'const bp = "xl:flex-row";',
        errors: [{ message: /breakpoint scale is closed/ }],
      },
      {
        code: 'const a = "bg-blue-500";',
        errors: [{ message: /palette/ }],
      },
      // Фокус краской действия: класс работает, выглядит правильным и связывает два
      // решения в одно. Единственный дефект из этой таблицы, который НИЧЕГО не портит на
      // экране — до первой перекраски кнопок.
      {
        code: 'const f = "focus-visible:outline-action-primary";',
        errors: [{ message: /focus indicator painted with the ACTION colour/ }],
      },
      {
        code: 'const b = "text-[#0066ff]";',
        errors: [{ message: /colour written into a class/ }],
      },
      {
        code: 'const c = "text-[12.5px]";',
        errors: [{ message: /type scale is closed/ }],
      },
      // The alpha channel: a named colour plus a modifier is a composite with no name,
      // and it is invisible to the contrast scan because that scan's ink pattern stops
      // at the `/`. Both spellings of the modifier, and a rung as well as a root.
      {
        code: 'const q = "border-action-primary bg-action-primary/[0.06]";',
        errors: [{ message: /alpha modifier on a named colour/ }],
      },
      {
        code: 'const r = "bg-canvas/40";',
        errors: [{ message: /alpha modifier on a named colour/ }],
      },
      {
        code: 'const s = "font-mono text-action-primary/70";',
        errors: [{ message: /alpha modifier on a named colour/ }],
      },
      {
        code: 'const s2 = "hover:bg-info-tint/50 text-content-subtle/80";',
        errors: [{ message: /alpha modifier on a named colour/ }],
      },
      // The style-object channel, which no class pattern could ever reach: the colour
      // is composed from a prop at the call site.
      {
        code: 'const u = { background: "rgba(11,11,12,0.45)" };',
        errors: [{ message: /colour function in a string/ }],
      },
      {
        code: 'const v = `rgba(11,11,12,${String(backdrop)})`;',
        errors: [{ message: /colour function in a string/ }],
      },
      {
        code: 'const w = { color: "hsl(210 100% 50%)" };',
        errors: [{ message: /colour function in a string/ }],
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
      // The role pattern: a rung and a grey in one class list, in either order, is the
      // spelling the twelve roles replaced.
      {
        code: 'const k = "mt-px text-tiny text-content-subtle";',
        errors: [{ message: /A rung plus a grey/ }],
      },
      {
        code: 'const l = "text-content-muted mb-md text-body";',
        errors: [{ message: /A rung plus a grey/ }],
      },
      {
        code: 'const m = "truncate text-body font-semibold text-content-primary";',
        errors: [{ message: /A rung plus a grey/ }],
      },
      // Padding used to buy the same exemption a fill does, on the grounds that a control
      // pads its own label. It bought it for 18 class lists that draw no box at all —
      // every empty, loading and error state in the app, spelled across three rungs and
      // three greys. A gap with a sentence in it is not a control.
      {
        code: 'const n = "px-lg py-empty text-center text-body text-content-subtle";',
        errors: [{ message: /A rung plus a grey/ }],
      },
      {
        code: 'const o = "py-[40px] text-center text-body text-content-muted";',
        errors: [{ message: /A rung plus a grey/ }],
      },
      {
        code: 'const p = "p-page text-body text-content-primary";',
        errors: [{ message: /A rung plus a grey/ }],
      },
      // The line-height axis. `[1.5]` is the interesting one: it was the single most
      // written value in the tree and every one of its sixteen sites was restating the
      // line-height the element already inherited from preflight.
      {
        code: 'const q = "type-dialog-body leading-[1.5]";',
        errors: [{ message: /already has a body line-height/ }],
      },
      {
        code: 'const r = "text-body leading-[1.45] md:leading-[1.7]";',
        errors: [{ message: /already has a body line-height/ }],
      },
      // The quiet half, and the reason the retired names are listed rather than left to
      // fail on their own: the scale is replaced, so these emit no rule at all and the
      // element keeps whatever it inherited. Nothing on screen says the name is gone.
      {
        code: 'const s = "text-tiny leading-snug";',
        errors: [{ message: /line-height scale is replaced/ }],
      },
      {
        code: 'const t = "leading-relaxed hover:leading-tight md:leading-6";',
        errors: [{ message: /line-height scale is replaced/ }],
      },
      // The letter-spacing axis. Above `shared/ui` there is one name and it is not a
      // typographic rung; type's own spacing belongs to the roles that declare it.
      {
        code: 'const v = "type-item-title tracking-[.04em]";',
        errors: [{ message: /Letter-spacing is not a scale/ }],
      },
      {
        code: 'const x = "text-tiny uppercase tracking-wide";',
        errors: [{ message: /letter-spacing scale is replaced/ }],
      },
    ],
  });
}
