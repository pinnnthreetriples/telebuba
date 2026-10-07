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
      'const a = "bg-action-primary text-on-fill px-6 py-3 rounded-full text-body";',
      'const b = "gap-3 mb-4 border-canvas shadow-pop duration-state";',
      // The written exceptions.
      {
        // Единственная пара имён с одним значением: белая поверхность и чернила на
        // заливке. Разные имена держит гейт контраста — см. шапку `semantic.ts`.
        code: 'const c = "bg-surface-card text-on-fill";',
        name: 'a white surface and ink on a fill keep two names',
      },
      {
        // Фокус — тот же синий, что у действия, и то же имя. `shadow-focus` — тень.
        code: 'const c2 = "focus-visible:outline-action-primary focus-visible:shadow-focus";',
        name: 'a focus ring is the action blue; the focus glow is an elevation',
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
      { code: 'const g = "p-0 m-0 w-0";', name: 'zero is not a step' },
      {
        code: 'const g1 = "p-1 px-2 gap-3 mt-4 py-6 lg:px-8 py-16 -mt-1 sm:-translate-x-1/2 left-1/2 inset-0";',
        name: 'the declared numeric rungs of the 4px grid, and fractions that are not rhythm',
      },
      {
        code: 'const g1b = "rounded-sm rounded-md rounded-lg rounded-full rounded-t-lg";',
        name: 'the three radii and the pill',
      },
      {
        code: 'const g2 = "bg-surface-card/85 bg-black/10 border-surface-card/40 border-black/5";',
        name: 'an alpha on white (`surface-card`) or black is a wash over content the palette cannot know',
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
        code: 'const t1 = "mt-1 type-small"; const t2 = "type-small text-danger";',
        name: 'a role, and a role recoloured',
      },
      {
        code: 'const t3 = "rounded-full border border-line bg-surface-card px-3 py-1 text-small text-content-muted";',
        name: 'a class list that paints a box is drawing a control',
      },
      {
        code: 'const t3b = "px-4 py-16 text-center type-body"; const t3c = "p-8 type-body text-content-primary";',
        name: 'a padded gap holding a role, with and without a colour override',
      },
      {
        code: 'const t4 = "text-body text-content-muted hover:text-action-primary";',
        name: 'a class list that reacts to the pointer is drawing a control',
      },
      {
        code: 'const t5 = "text-body leading-none text-content-subtle"; const t6 = "absolute left-4 text-body text-content-subtle";',
        name: 'at `lead` the scale doubles as a glyph size',
      },
      {
        code: 'const t7 = "min-w-badge text-body font-medium";',
        name: "a weight with no grey is a number's emphasis, which no role can absorb",
      },
      // The two line-heights and the one letter-spacing that are left, and the two
      // things the line-height pattern deliberately cannot reach.
      {
        code: 'const u1 = "tracking-code";',
        name: 'the named rungs, which is the whole point',
      },
      {
        code: 'const u2 = "text-body leading-none text-on-fill";',
        name: 'a single glyph has no line-height, and `none` is the rung that says so',
      },
      {
        code: 'const u3 = "h-[1.1em] type-h1 leading-[1.1em] tabular-nums";',
        name: "the odometer's line-height is measured against the text, like the box beside it",
      },
      {
        code: 'const u4 = "text-small font-medium uppercase tracking-[0.04em] text-content-subtle";',
        filename: 'src/shared/ui/DataTable.tsx',
        name: '`shared/ui` composes a column label by hand, letter-spacing included',
      },
      {
        code: 'const sp2 = "inline-flex tb-spin";',
        name: '`tb-spin` alone is the refresh icon turning, which is not a ring',
      },
    ],
    invalid: [
      // The 1px hairline left every scale at once; a nudge is dropped, a divider is a border.
      ...['mt-px', 'py-px', 'gap-px', 'md:px-px', '-mt-px', 'h-px', 'w-px'].map((cls) => ({
        code: `const hair = "flex ${cls}";`,
        errors: [{ message: /no 1px rung/ }],
      })),
      // Старая типографика: имя не выпускает правила, и текст молча падает на то, что
      // унаследовал.
      {
        code: 'const ot1 = "type-caption"; ',
        errors: [{ message: /rebuilt into five steps/ }],
      },
      {
        code: 'const ot2 = "md:text-tiny";',
        errors: [{ message: /rebuilt into five steps/ }],
      },
      {
        code: 'const ot3 = "truncate leading-stack";',
        errors: [{ message: /rebuilt into five steps/ }],
      },
      {
        code: 'const ow = "hover:font-semibold"; const ow2 = "font-bold";',
        errors: [{ message: /Two weights/ }, { message: /Two weights/ }],
      },
      {
        code: 'const wm = "text-h3 font-medium tracking-[-0.01em]";',
        errors: [{ message: /optical tracking of each type step/ }],
      },
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
      // Кольцо руками: анимация плюс окрашенная верхняя граница. Один `tb-spin` — нет.
      {
        code: 'const sp = "tb-spin inline-block size-spinner rounded-full border-2 border-line border-t-action-primary";',
        errors: [{ message: /waiting ring assembled by hand/ }],
      },
      // Направленная краска: `border-black` правило видело всегда, `border-r-black` — нет.
      {
        code: 'const dir = "border-r-black";',
        errors: [{ message: /Bare `black`/ }],
      },
      // Восьмой проход: вторые имена одной краски — ошибка, с альфой и без.
      {
        code: 'const w1 = "border-t-white"; const w2 = "bg-white/85";',
        errors: [
          { message: /collapsed into another one/ },
          { message: /collapsed into another one/ },
        ],
      },
      {
        code: 'const o1 = "text-on-action"; const o2 = "stroke-on-success"; const o3 = "hover:text-on-inverse";',
        errors: [
          { message: /collapsed into another one/ },
          { message: /collapsed into another one/ },
          { message: /collapsed into another one/ },
        ],
      },
      {
        code: 'const h1 = "hover:bg-action-hover"; const h2 = "border-line-row"; const h3 = "from-fallback-start";',
        errors: [
          { message: /collapsed into another one/ },
          { message: /collapsed into another one/ },
          { message: /collapsed into another one/ },
        ],
      },
      {
        code: 'const fo = "focus-visible:outline-focus"; const fo2 = "focus:border-focus";',
        errors: [
          { message: /second name for the action blue/ },
          { message: /second name for the action blue/ },
        ],
      },
      {
        code: 'const bp = "xl:flex-row";',
        errors: [{ message: /breakpoint scale is closed/ }],
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
        // Исключение для белого — ровно `surface-card`, а не весь `surface`.
        code: 'const r2 = "bg-surface/60";',
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
      // The rhythm is Firecrawl's numeric grid: a declared key is legal (see `valid`), an
      // undeclared one and every retired name emit no rule and are errors.
      {
        code: 'const d = "px-5 py-2";',
        errors: [{ message: /closed 4px grid/ }],
      },
      {
        code: 'const d2 = "gap-0.5";',
        errors: [{ message: /closed 4px grid/ }],
      },
      {
        code: 'const d3 = "sm:-mt-7";',
        errors: [{ message: /closed 4px grid/ }],
      },
      {
        code: 'const d4 = "gap-tight";',
        errors: [{ message: /named rungs are gone/ }],
      },
      {
        code: 'const d5 = "hover:p-md";',
        errors: [{ message: /named rungs are gone/ }],
      },
      {
        code: 'const d6 = "-mt-hair";',
        errors: [{ message: /named rungs are gone/ }],
      },
      {
        code: 'const d7 = "scroll-mt-page top-xl";',
        errors: [{ message: /named rungs are gone/ }],
      },
      {
        code: 'const e = "gap-[11px]";',
        errors: [{ message: /rung near this value/ }],
      },
      {
        code: 'const f = "rounded-[9px]";',
        errors: [{ message: /Three radii/ }],
      },
      {
        code: 'const f2 = "rounded-card";',
        errors: [{ message: /`card` is gone/ }],
      },
      {
        code: 'const f3 = "md:rounded-t-card";',
        errors: [{ message: /`card` is gone/ }],
      },
      {
        code: 'const g = "duration-[420ms]";',
        errors: [{ message: /Four motion rungs/ }],
      },
      // The exemption this rule used to carry, now the pattern it enforces: while
      // `w-*`/`h-*` in pixels were allowed, 73 distinct dimensions grew beside the
      // rhythm's rungs.
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
        code: 'const k = "mt-1 text-small text-content-subtle";',
        errors: [{ message: /A step plus a grey/ }],
      },
      {
        code: 'const l = "text-content-muted mb-3 text-body";',
        errors: [{ message: /A step plus a grey/ }],
      },
      {
        code: 'const m = "truncate text-body font-medium text-content-primary";',
        errors: [{ message: /A step plus a grey/ }],
      },
      // Padding used to buy the same exemption a fill does, on the grounds that a control
      // pads its own label. It bought it for 18 class lists that draw no box at all —
      // every empty, loading and error state in the app, spelled across three rungs and
      // three greys. A gap with a sentence in it is not a control.
      {
        code: 'const n = "px-4 py-16 text-center text-body text-content-subtle";',
        errors: [{ message: /A step plus a grey/ }],
      },
      {
        code: 'const o = "py-[40px] text-center text-body text-content-muted";',
        errors: [{ message: /A step plus a grey/ }],
      },
      {
        code: 'const p = "p-8 text-body text-content-primary";',
        errors: [{ message: /A step plus a grey/ }],
      },
      // The line-height axis. `[1.5]` is the interesting one: it was the single most
      // written value in the tree and every one of its sixteen sites was restating the
      // line-height the element already inherited from preflight.
      {
        code: 'const q = "type-body text-content-muted leading-[1.5]";',
        errors: [{ message: /Line-height belongs to the type step/ }],
      },
      {
        code: 'const r = "text-body leading-[1.45] md:leading-[1.7]";',
        errors: [{ message: /Line-height belongs to the type step/ }],
      },
      // The quiet half, and the reason the retired names are listed rather than left to
      // fail on their own: the scale is replaced, so these emit no rule at all and the
      // element keeps whatever it inherited. Nothing on screen says the name is gone.
      {
        code: 'const s = "text-small leading-snug";',
        errors: [{ message: /line-height scale is replaced/ }],
      },
      {
        code: 'const t = "leading-relaxed hover:leading-tight md:leading-6";',
        errors: [{ message: /line-height scale is replaced/ }],
      },
      // The letter-spacing axis. Above `shared/ui` there is one name and it is not a
      // typographic rung; type's own spacing belongs to the roles that declare it.
      {
        code: 'const v = "type-body-medium tracking-[.04em]";',
        errors: [{ message: /Letter-spacing is not a scale/ }],
      },
      {
        code: 'const x = "text-small uppercase tracking-wide";',
        errors: [{ message: /letter-spacing scale is replaced/ }],
      },
      // What Tailwind 4 paints without asking the theme. Each emitted nothing under
      // Tailwind 3, so each is a class that has never done what it says.
      ...[
        'h-auto',
        'w-fit',
        'size-full',
        'max-h-full',
        'w-1/2',
        'sm:min-h-0',
        'min-h-control',
        '!w-screen',
        'max-w-col',
      ].map((cls) => ({
        code: `const dim = "flex ${cls}";`,
        errors: [{ message: /Dimensions are closed scales of their own/ }],
      })),
      ...['z-50', 'duration-300', 'scale-95', 'hover:scale-105', 'z-5'].map((cls) => ({
        code: `const bare = "relative ${cls}";`,
        errors: [{ message: /accepts any bare number/ }],
      })),
      {
        code: 'const sq = "rounded-none md:rounded-t-none";',
        errors: [{ message: /no zero radius on the scale/ }],
      },
    ],
  });

  // The other half of the same patterns: every rung the dimension and motion scales DO
  // declare stays legal, so the closure cannot drift into flagging the system itself.
  ruleTester.run('no-raw-values: declared rungs', rule, {
    valid: [
      'const w1 = "w-0 w-auto w-max w-full w-col w-logAccount w-table";',
      'const h1 = "h-full h-rail h-profileDialog min-h-touch min-h-screen max-h-feedInline";',
      'const m1 = "min-w-0 min-w-table max-w-full max-w-shell size-face size-tick";',
      'const z1 = "z-0 z-pop z-toast duration-state duration-pulse scale-press active:scale-rest";',
      'const a1 = "max-w-[84%] h-[1.1em] w-[min(84vw,300px)]";',
    ],
    invalid: [],
  });
}
