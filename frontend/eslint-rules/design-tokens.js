// The design system is a closed set — tailwind.config.ts names every colour, type
// rung, radius, elevation, motion rung, line-height, letter-spacing and unit of rhythm
// the UI has — and a closed set only stays closed if reopening it is an error rather
// than a habit.
//
// Every pattern below flags ZERO sites in the tree it landed on, which is the bar the
// repo's other custom rule set: a rule that has to be suppressed to pass is not a
// rule, it is a warning with extra steps.
//
// It reads string literals and template chunks anywhere, not only in `className`: a
// style constant hoisted to the top of a module is the same decision written
// somewhere else, and that is exactly where the drift used to hide (eight files held
// a copy of one field's classes under four different names).
//
// What this rule deliberately does NOT flag, and why:
//
//   `bg-white` / `text-white` (213 sites) — white and black are the two colours a
//   palette does not have to name. An alias would be a synonym rather than a role,
//   and there is no second theme for it to point somewhere else in.
//
//   `rounded-[1px|2px|3px]` (24 sites) — a hairline's radius. Snapping a 2px progress
//   bar or a chat bubble's tail up to the 6px rung would round it away, so the radius
//   pattern starts at 4px.
//
//   arbitrary spacing above 34px (10 sites) — the rhythm is dense from 2 to 32px and
//   that is the range the pattern covers. Above it are a page's own breathing room
//   and the room a control takes up inside a field, one-offs by nature.
//
//   a dimension measured against the viewport or the text (`w-[min(84vw,300px)]`,
//   `max-w-[90vw]`, `h-[1.1em]`) — those are not values the design system could hold,
//   because they resolve differently on every screen. Anything in px or rem is.
//
//   a dimension only one component ever asks for (12 sites) — AddStoryModal's
//   collage-layout tile and story preview, NeuroAccountsModal's spend gauge (its bars
//   and the row they stand in), Switch's track, AccountsTable's trust bar,
//   AccountsPage's search pill, WarmingBoard's embedded log, LoginPage's card,
//   SettingsPage's settings column, ProxyPool's empty-state sentence and ScenarioCard's
//   prompt column. A dimension owned by one component's internal layout is that
//   component's business, not the scale's: giving it a rung would put a name with a
//   single wearer in the canon, which is a literal with a name and the way a closed set
//   reopens. Each of the twelve carries its own inline suppression rather than a hole in
//   the pattern, so the second component to reach for the same measurement is flagged
//   and has to argue for a rung.
//
//   `leading-none` (8 sites) — a declared rung, and deliberately not a text one. Its
//   wearers are single characters, not lines: the `×` that removes a chip in five
//   places, HelpHint's `?`, and WarmDaysModal's 42px day count. It is also the marker
//   the type-role pattern below reads to tell a glyph from text, which is the second
//   reason it keeps Tailwind's name.
//
//   `leading-[1.1em]` (2 sites, Odometer) — the em carve-out, and the only one the
//   line-height pattern makes. It is the same argument the dimension pattern already
//   makes for `h-[1.1em]`, which is the class sitting beside it: this is a measurement
//   against the text, not a rung a design system could hold. Concretely it is a
//   geometric constant said in four places — the column's height, each digit cell's
//   height, this line-height, and the `translateY(-n * 1.1em)` that rolls the digit into
//   place. Round the line-height to a typographic rung and the digits stop landing. The
//   two sites used to spell it `[1.1]` and `[1.1em]`; they are one spelling now, so the
//   carve-out has one shape to allow rather than two.
//
//   `tracking-[-0.01em]` on the wordmark (2 sites, `AppNav` and `NavDrawer`) — the mark
//   is drawn from the design source `Telebuba.dc.html`, and that spacing is a value the
//   source sets, not one this app chose. The sweep that closed this axis dropped it as
//   sub-threshold, which it is — 0.16px per character — and that is the wrong test to
//   apply: a lint rule about typographic scales has no standing over a brand mark. The
//   same file already refuses to give the wordmark a type role for the same reason, so
//   this is the second half of one decision rather than a new exception.
//
//   `tracking-[…]` inside `shared/ui` (2 sites, DataTable's `TH` and `CARD_LABEL`) —
//   the same `above` carve-out the type-role pattern makes, for the same reason:
//   `shared/ui` is the layer allowed to compose primitives by hand. Both are
//   `text-tiny font-medium uppercase tracking-[0.04em] text-ink-subtle`, which is
//   `type-eyebrow` exactly except for the weight — the role is 600 and these are 500.
//   That is worth knowing and is NOT worth fixing here: moving them onto the role would
//   change what a table header looks like in every table in the app, to make a role fit.
//   Left as it is, on purpose, and written down so the next reader does not rediscover it.
//
//   two decorative gradients (ProfileModal, _profileShared) — placeholder fills
//   behind an avatar or a thumbnail that has not loaded. They exist only to differ
//   from each other; naming them would put two single-use roles in the canon and
//   imply the UI means something by them. Both carry an inline suppression.

// The type-role pattern below is the one rule here that does not apply everywhere, and
// the exception is the point rather than a hole. `shared/ui` is the layer allowed to
// compose primitives by hand: `Button` deciding that its label is 13px semibold IS the
// design system, said in the place the system is kept. Every layer above it —
// `pages/`, `widgets/`, `features/`, `entities/`, `routes/` — is a consumer, and a
// consumer respelling a rung, a weight and a grey is how one job came to have three
// spellings. Above `shared/ui` a page names the ROLE the text plays.
// A test file is exempt too, and for the opposite reason: `cn.test.ts` and
// `designTokenRule.test.ts` assert on the very spellings this bans, and a fixture is
// data about the code rather than a decision inside it.
const NOT_A_CONSUMER = /(?:^|[\\/])src[\\/]shared[\\/]ui[\\/]|\.test\.tsx?$/;

// `hero` is left out on purpose. The config calls it "the one empty-state numeral" and
// means it: one element in the whole app is 42px, WarmDaysModal's day count. A role
// needs two wearers in two slices — a rung worn once is a literal with a name — so
// there is no `type-*` for it to move onto, and flagging it would be asking for a
// thirteenth role with nothing to defend it.
const TYPE_RUNG = 'micro|tiny|body|lead|title|stat|display';
const INK_RAMP = String.raw`text-ink(?:-(?:body|muted|subtle))?(?![\w-])`;

// What this pattern deliberately does NOT reach, and why:
//
//   a class list that also paints a box — a fill, a border, a radius, an elevation or a
//   focus ring. That is a CONTROL being drawn, and a control's face is its own business:
//   a status pill, a hand-written button, a glyph badge, a field. Those belong to
//   `Badge`, `Button` and `Input`, and the ~100 drawn by hand above `shared/ui` are a
//   layering debt to pay by moving them down — not by inventing `type-pill` and
//   `type-control`, which would put the debt in the canon and call it design.
//
//   Padding used to be on that list and is not any more. It was there because a control
//   pads its own label, but padding paints nothing: it is the commonest utility in the
//   app, so `p-*` alone exempted 18 class lists that draw no box at all. Every one was a
//   page's empty, loading or error state — a centred sentence in a padded gap — written
//   across three rungs (`lead` eight times, `body` nine, `micro` once) and three greys.
//   That is the exact drift this pattern exists to stop, let through by the one prefix
//   that says nothing about whether a box is being drawn.
//
//   a class list that reacts to the pointer (`hover:`, `focus`, `active:`, `disabled:`,
//   `transition`, `cursor-`). Same category, reached from the other side: three of them
//   draw no box but are still controls — a text button, a nav tab, a tooltip trigger.
//
//   a class list carrying `leading-none`, `absolute` or `fixed`. At `lead` the type
//   scale doubles as a GLYPH size, exactly the way `IconButton` wears `text-title` to
//   size a `×`: all six such sites in the tree are one character — the `×` that removes
//   a chip in CreateCampaignModal, CampaignsCard and WarmingPage, and the `@` prefix
//   inside the username fields of ChannelCreateModal and ProfileModal. A glyph is not
//   text playing a role, and no `type-*` should pretend it is.
//
//   a rung beside a WEIGHT with no grey in the list. Measured, not assumed: that
//   variant flags seven sites no role can honestly absorb, and every one of them is a
//   number — a trust score, a spend gauge, a tile's figure, an avatar's initials —
//   where the weight is the figure's emphasis and not a heading's. A pattern cannot
//   tell a bold heading from a bold number, so the weight half of the canon is carried
//   by the role table and its documentation rather than by this gate.
const PAINTS_A_BOX = String.raw`(?:^|\s)(?:[\w-]+:)*(?:bg-|border(?![\w-])|border-|rounded|shadow-|ring-)`;
const IS_A_CONTROL = String.raw`(?:^|\s)(?:hover|focus|focus-visible|focus-within|active|disabled|aria-[\w-]+|data-[\w-]+):|(?:^|\s)transition|(?:^|\s)cursor-`;
const IS_A_GLYPH = String.raw`(?:^|\s)(?:leading-none|absolute|fixed)(?![\w-])`;

const PALETTE =
  'slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose';
const COLOUR = 'bg|text|border|ring|fill|stroke|from|to|via|divide|outline|decoration|caret|accent';
const SPACE = 'p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|gap-x|gap-y|space-x|space-y';
const DIMENSION = 'size|min-w|max-w|min-h|max-h|w|h';

// A utility class starts at the beginning of the string or after whitespace. Anchored
// so `sub-p-2` or a URL that happens to contain `to-3` is not a hit.
const at = (body) => new RegExp(String.raw`(?:^|\s)(?:${body})`);

// Colour names the canon collapsed into another. Listed rather than left to fail on
// its own, because only half of them fail visibly: an unknown `bg-*` emits no rule at
// all and the chip loses its fill, which anyone reviewing the screen sees — but an
// unknown `border-*` on an element that also carries `border` falls through to
// preflight's own default, Tailwind's `gray-200`, three units from `line` and cool
// where the app is warm. That one comes back looking right.
const RETIRED = 'track|line-input|primary-wash|success-dot';

// Tailwind's own line-height and letter-spacing names, which this config replaces
// outright the way it replaced the type scale. They are listed rather than left to fail
// on their own for the reason the retired colours are: an unknown utility emits no rule
// at all, so `leading-relaxed` after this change is not an error, it is a class that
// silently does nothing and leaves the element at whatever it inherited. Half of these
// were in the tree — `snug` on three explanations, `relaxed` on two, `tight` on an
// account name — and none of them would have announced its own removal.
const RETIRED_LEADING = '10|3|4|5|6|7|8|9|tight|snug|normal|relaxed|loose';
const RETIRED_TRACKING = 'tighter|tight|normal|wide|wider|widest';

const PATTERNS = [
  {
    test: at(String.raw`(?:[\w-]+:)*(?:${COLOUR})-(?:${RETIRED})(?![\w-])`),
    message:
      'That colour was collapsed into another one and no longer exists: `track` and `primary-wash` are `canvas` and `primary-tint`, `line-input` is `line`, `success-dot` is `success`. The unification ledger in docs/design-system.html carries the reason for each.',
  },
  {
    test: at(String.raw`(?:${COLOUR})-(?:${PALETTE})-\d{2,3}(?![\w-])`),
    message:
      "Tailwind's own palette is not this app's. The config adds a named set beside it, so `bg-blue-500` would sit next to `bg-primary` and disagree with it by a shade nobody chose. Use the semantic colour.",
  },
  {
    test: at(
      String.raw`(?:bg|text|border|ring|fill|stroke|from|to|via|shadow)-\[(?:#|rgb|hsl|oklch)`,
    ),
    message:
      'A colour written into a class is a colour the design system does not know about. Name it in tailwind.config.ts — every colour there carries its role and, where it is text, its measured contrast — and use that name.',
  },
  {
    test: at(String.raw`text-\[[0-9.]+(?:px|rem|em)\]`),
    message:
      'The type scale is closed: eight rungs from `text-micro` to `text-hero`, replacing Tailwind’s outright. A ninth size written in pixels is the drift those rungs were introduced to end.',
  },
  {
    test: at(String.raw`(?:${SPACE})-(?!0(?![\d.]))[0-9.]+(?![\w[])`),
    message:
      "Tailwind's numeric spacing is a 4px grid; this app's rhythm is the design's own (`gap-md` is 10px, not 8 or 12). Mixing them is how `gap-md` came to sit beside `px-3` in one row. Use the named rung.",
  },
  {
    test: at(String.raw`(?:${SPACE})-\[(?:[0-9]|[12][0-9]|3[0-4])px\]`),
    message:
      'The rhythm has a rung within 2px of this value. Reach for it: twelve names is the whole point, and a thirteenth measurement in pixels is where two rhythms start again.',
  },
  {
    test: at(String.raw`(?:[\w-]+:)*(?:${DIMENSION})-\[[0-9.]+(?:px|rem)\](?![\w-])`),
    message:
      'Dimensions are their own scale now: `size-*` for a square, `width`/`height` for everything else, and each rung is named for the component that wears it. This rule used to exempt `w-*`/`h-*` in pixels on the grounds that a component’s size is not a rung of the rhythm — which was true, and is exactly how 73 distinct dimensions grew beside eleven rungs. Both halves are scales now, so a measurement here belongs in one of them.',
  },
  {
    test: at(String.raw`rounded(?:-[a-z]+)?-\[(?:[4-9]|[1-9][0-9])`),
    message:
      'Five radii, each named for what wears it (`sm` inside a box, `md` a standalone control, `lg` a panel nested in a card, `card` the card, `full` the pill). Hairlines under 4px keep their own value; anything larger has a rung.',
  },
  {
    test: at(String.raw`duration-\[`),
    message:
      'Four motion rungs, one per kind of gesture (`state`, `enter`, `reveal`, `roll`). A duration in milliseconds is how one gesture came to run 420ms on one element against 400ms on the other.',
  },
  {
    test: at(String.raw`(?:[\w-]+:)*leading-\[[0-9.]+(?:px|rem)?\](?![\w-])`),
    message:
      'This app already has a body line-height and it is not written anywhere: preflight sets `html { line-height: 1.5 }` and the type rungs are bare strings, so everything inherits it. Sixteen sites wrote `leading-[1.5]` and every one was restating the value it already had; the rest spent 1.35, 1.375, 1.4, 1.45, 1.6 and 1.625 on the one job of setting a sentence. Delete the class and inherit, or use `leading-stack` (a heading over its own detail line) or `leading-log` (a monospace stream on a `term` surface).',
  },
  {
    test: at(String.raw`(?:[\w-]+:)*leading-(?:${RETIRED_LEADING})(?![\w-])`),
    message:
      'Tailwind’s line-height scale is replaced, so this name no longer emits a rule — the element silently keeps whatever it inherited rather than failing visibly. Three rungs are left: `none` for a single glyph, `stack` for a heading over its own detail line, `log` for a monospace stream. A sentence needs none of them; it inherits 1.5 already.',
  },
  {
    above: NOT_A_CONSUMER,
    // The wordmark keeps the source's own spacing; see the header.
    unless: /tracking-\[-0\.01em\]/,
    test: at(String.raw`(?:[\w-]+:)*tracking-\[[^\]]*\]`),
    message:
      'Letter-spacing is not a scale in this app, and that is the decision rather than an omission: the two values type actually spends are declared by the roles that need them — `type-eyebrow` carries 0.04em, `type-page-title` carries -0.02em — and a `tracking-*` rung for either would be a second way to say what the role already says. `tracking-code` is the one name, and it is a field’s affordance rather than typography: the spacing that lets a one-time code be read back character by character as it is typed.',
  },
  {
    test: at(String.raw`(?:[\w-]+:)*tracking-(?:${RETIRED_TRACKING})(?![\w-])`),
    message:
      'Tailwind’s letter-spacing scale is replaced, so this name emits nothing and the element silently keeps the spacing it inherited. `tracking-code` is the only rung; type’s own spacing belongs to `type-eyebrow` and `type-page-title`.',
  },
  {
    above: NOT_A_CONSUMER,
    unless: new RegExp(`${PAINTS_A_BOX}|${IS_A_CONTROL}|${IS_A_GLYPH}`),
    test: new RegExp(
      // A rung and a grey from the ink ramp, in either order, anywhere in one class list.
      String.raw`(?:^|\s)(?:[\w-]+:)*text-(?:${TYPE_RUNG})(?![\w-])[\s\S]*(?:^|\s)(?:[\w-]+:)*${INK_RAMP}` +
        String.raw`|(?:^|\s)(?:[\w-]+:)*${INK_RAMP}[\s\S]*(?:^|\s)(?:[\w-]+:)*text-(?:${TYPE_RUNG})(?![\w-])`,
    ),
    message:
      'A rung plus a grey is a role spelled out, and spelling it out is how one job came to have three spellings: the same small caption was written `ink-subtle` 53 times, `ink-muted` 13 times, and with no colour at all 9 times — three greys nobody chose between. Above `shared/ui` the page names the role instead: `type-page-title`, `type-dialog-title`, `type-dialog-body`, `type-card-title`, `type-item-title`, `type-eyebrow`, `type-label`, `type-value`, `type-prose`, `type-caption`, `type-meta`, `type-stat`. They are declared as `typeRole` in tailwind.config.ts, each with the one sentence it has to answer to. A role plus an override — `type-caption text-danger`, `type-meta font-bold` — is the intended way to say the same text in another colour or another weight.',
  },
];

/** @type {import('eslint').Rule.RuleModule} */
const noRawValues = {
  meta: {
    type: 'problem',
    docs: { description: 'Design values come from the Tailwind config, not from the call site.' },
    schema: [],
  },
  create(context) {
    const filename = context.filename ?? context.getFilename();
    const check = (node, text) => {
      if (typeof text !== 'string' || text.length === 0) return;
      for (const { test, message, above, unless } of PATTERNS) {
        if (above !== undefined && above.test(filename)) continue;
        if (unless !== undefined && unless.test(text)) continue;
        const hit = test.exec(text);
        if (hit) {
          context.report({ node, message: `${message}\n  found: ${hit[0].trim()}` });
          return;
        }
      }
    };
    return {
      Literal: (node) => {
        check(node, node.value);
      },
      TemplateElement: (node) => {
        check(node, node.value.raw);
      },
    };
  },
};

export default { rules: { 'no-raw-values': noRawValues } };
