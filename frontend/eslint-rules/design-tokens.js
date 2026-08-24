// The design system is a closed set — tailwind.config.ts names every colour, type
// rung, radius, elevation, motion rung and unit of rhythm the UI has — and a closed
// set only stays closed if reopening it is an error rather than a habit.
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
//   `w-*` / `h-*` in pixels — a 34px avatar or a 6px progress bar is a component's
//   dimension, not a rung of the app's rhythm.
//
//   two decorative gradients (ProfileModal, _profileShared) — placeholder fills
//   behind an avatar or a thumbnail that has not loaded. They exist only to differ
//   from each other; naming them would put two single-use roles in the canon and
//   imply the UI means something by them. Both carry an inline suppression.

const PALETTE =
  'slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose';
const COLOUR = 'bg|text|border|ring|fill|stroke|from|to|via|divide|outline|decoration|caret|accent';
const SPACE = 'p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|gap-x|gap-y|space-x|space-y';

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
    test: at(String.raw`rounded(?:-[a-z]+)?-\[(?:[4-9]|[1-9][0-9])`),
    message:
      'Five radii, each named for what wears it (`sm` inside a box, `md` a standalone control, `lg` a panel nested in a card, `card` the card, `full` the pill). Hairlines under 4px keep their own value; anything larger has a rung.',
  },
  {
    test: at(String.raw`duration-\[`),
    message:
      'Four motion rungs, one per kind of gesture (`state`, `enter`, `reveal`, `roll`). A duration in milliseconds is how one gesture came to run 420ms on one element against 400ms on the other.',
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
    const check = (node, text) => {
      if (typeof text !== 'string' || text.length === 0) return;
      for (const { test, message } of PATTERNS) {
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
