import { expect, test } from 'vitest';

import config from '../../../tailwind.config';

// WCAG 2.1 AA asks 4.5:1 of text under 18.66px bold / 24px regular. Every type rung
// this app has is under that, so 4.5:1 is the floor for all of them — there is no
// "large text" exception to lean on here. 1.4.11 asks 3:1 of a graphic that carries
// meaning, which is what an element holding nothing but an icon is.
const AA = 4.5;
const NON_TEXT = 3;

type Ramp = Record<string, string> & { DEFAULT?: string };
const colors = config.theme?.colors as Record<string, string | Ramp>;

// The palette as a class list spells it: `ink`, `ink-subtle`, `primary-tint`. The
// config nests the ramps, so DEFAULT loses its rung on the way out. Anything that is
// not a flat hex has no ratio to measure — `scrim` is an rgba wash over a photograph,
// and `transparent`/`current` are keywords rather than colours.
//
// `white` used to be seeded here by hand, because the palette lived in `theme.extend`
// and white was Tailwind's. Now that the palette REPLACES Tailwind's it carries its own
// white, and this table reads it like every other rung — which is the point of the move:
// a colour the app paints and a colour this gate measures can no longer be two sets.
const HEX: Record<string, string> = {};
for (const [name, value] of Object.entries(colors)) {
  if (typeof value === 'string') {
    if (value.startsWith('#')) HEX[name] = value;
    continue;
  }
  for (const [rung, hex] of Object.entries(value)) {
    if (hex.startsWith('#')) HEX[rung === 'DEFAULT' ? name : `${name}-${rung}`] = hex;
  }
}

// A `type-*` utility carries its own ink, so a role is a colour decision even where no
// `text-*` class is written. Read off the config so the two cannot drift.
const ROLE_INK: Record<string, string> = Object.fromEntries(
  Object.entries(config.theme?.typeRole as Record<string, { ink: string }>).map(([name, role]) => [
    name,
    role.ink,
  ]),
);

function channel(byte: number): number {
  const c = byte / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function luminance(hex: string): number {
  const value = hex.replace('#', '');
  const [r, g, b] = [0, 2, 4].map((i) => channel(parseInt(value.slice(i, i + 2), 16))) as [
    number,
    number,
    number,
  ];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(text: string, background: string): number {
  const ink = HEX[text];
  const fill = HEX[background];
  if (ink === undefined || fill === undefined) {
    throw new Error(`no such colour token: ${text} / ${background}`);
  }
  const [a, b] = [luminance(ink), luminance(fill)];
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

// The pairings that used to ship: each one is a real fill/text combination that
// measured under the floor, and the reason the `deep` rungs exist. Asserting they
// still fail is what stops the fix from being quietly reverted by "restoring" the
// brighter colour — the token would go back to a value this test rejects.
test('the rungs that failed are the ones the deep rungs replaced', () => {
  expect(ratio('success', 'success-tint')).toBeLessThan(AA);
  expect(ratio('warning', 'warning-tint')).toBeLessThan(AA);
  expect(ratio('primary', 'primary-tint')).toBeLessThan(AA);
  expect(ratio('danger', 'danger-tint')).toBeLessThan(AA);
});

// ---------------------------------------------------------------------------
// Reading the pairings off the source rather than listing them.
//
// The list used to be written by hand, under a comment claiming "a pairing that is not
// here is not painted anywhere". Nothing enforced that and it was not true: it carried
// `white on success.deep` and not `white on success.press`, which shipped on the promote
// button at 4.32:1 — a number the config itself printed as if it were the fix. It also
// carried two pairings nothing paints any more. So the table is derived now, and the
// only thing left by hand is the one kind of pairing a class scan structurally cannot
// see (below).
//
// The scan this replaces read one LINE at a time and asked whether it held both
// `bg-{tone}-tint` and that tone's failing text rung. Three things were invisible to it:
//   - a fill on a container and the text INSIDE it, which is the ordinary way a tinted
//     panel is written and how WarmingBoard's card painted `text-primary` on
//     `bg-primary-tint` at 4.38:1 in three places;
//   - a state: `hover:bg-*` and the label it lands under are one class list but not one
//     pairing a per-tone scan looks for;
//   - every fill that is not a `tint` — `bg-*-line` is used as a fill twice.
// ---------------------------------------------------------------------------

type Chunk = { text: string; at: number; always: boolean };

// One chunk is one set of classes that always ship together. A template literal's
// static parts are one chunk; each string inside an interpolation is its own, and is
// NOT always applied — two branches of a ternary never paint at the same time, so
// pairing one branch's fill with another's ink invents a combination nothing renders.
// Comments are skipped: an apostrophe in a prose comment opens a string that runs to
// the next one and swallows whatever class lists lie between.
function chunks(src: string, from = 0, to = src.length, always = true): Chunk[] {
  const out: Chunk[] = [];
  let i = from;
  while (i < to) {
    const c = src[i];
    if (c === '/' && src[i + 1] === '/') {
      const nl = src.indexOf('\n', i);
      if (nl === -1) break;
      i = nl;
    } else if (c === '/' && src[i + 1] === '*') {
      const close = src.indexOf('*/', i + 2);
      i = close === -1 ? to : close + 2;
    } else if (c === '"' || c === "'") {
      let j = i + 1;
      while (j < to && src[j] !== c && src[j] !== '\n') j += src[j] === '\\' ? 2 : 1;
      out.push({ text: src.slice(i + 1, j), at: i, always });
      i = j + 1;
    } else if (c === '`') {
      let j = i + 1;
      let statics = '';
      while (j < to && src[j] !== '`') {
        if (src[j] === '\\') {
          j += 2;
          continue;
        }
        if (src[j] === '$' && src[j + 1] === '{') {
          let depth = 1;
          let k = j + 2;
          while (k < to && depth > 0) {
            if (src[k] === '{') depth += 1;
            else if (src[k] === '}') depth -= 1;
            k += 1;
          }
          out.push(...chunks(src, j + 2, k - 1, false));
          j = k;
          continue;
        }
        statics += src[j];
        j += 1;
      }
      out.push({ text: statics, at: i, always });
      i = j + 1;
    } else {
      i += 1;
    }
  }
  return out.filter((chunk) => chunk.text.trim() !== '');
}

const TAG_NAME = /^[A-Za-z_$][\w.$]*/;

// Walk an opening tag to its OWN `>`: a `>` inside a prop expression, a string or a
// comment is not the end of the tag, and a windowed regex cannot tell the difference.
function walkTag(src: string, start: number): { end: number; name: string } | null {
  const name = TAG_NAME.exec(src.slice(start + 1, start + 60))?.[0];
  if (name === undefined) return null;
  let i = start + 1 + name.length;
  let depth = 0;
  let quote = '';
  while (i < src.length) {
    const c = src[i] ?? '';
    if (quote !== '') {
      if (c === '\\') i += 1;
      else if (c === quote || (c === '\n' && quote !== '`')) quote = '';
    } else if (c === '/' && src[i + 1] === '/') {
      const nl = src.indexOf('\n', i);
      if (nl === -1) return null;
      i = nl;
    } else if (c === '/' && src[i + 1] === '*') {
      const close = src.indexOf('*/', i + 2);
      if (close === -1) return null;
      i = close + 1;
    } else if (c === '"' || c === "'" || c === '`') quote = c;
    else if (c === '{') depth += 1;
    else if (c === '}') depth -= 1;
    else if (c === '>' && depth === 0) return { end: i, name };
    i += 1;
  }
  return null;
}

/** Where the element's subtree ends: past `/>`, or past its own closing tag. */
function elementEnd(src: string, openEnd: number, name: string): number {
  if (src[openEnd - 1] === '/') return openEnd + 1;
  const close = `</${name}`;
  let depth = 1;
  let i = openEnd + 1;
  while (i < src.length) {
    if (src[i] === '<') {
      if (src.startsWith(close, i) && !/[\w.$]/.test(src[i + close.length] ?? '')) {
        depth -= 1;
        const gt = src.indexOf('>', i);
        if (depth === 0) return gt + 1;
        i = gt + 1;
        continue;
      }
      const inner = walkTag(src, i);
      if (inner && inner.name === name) {
        if (src[inner.end - 1] !== '/') depth += 1;
        i = inner.end + 1;
        continue;
      }
    }
    i += 1;
  }
  return src.length;
}

// `LayoutIcon` joined the list when AddStoryModal's collage tile stopped painting its
// selected fill as `bg-primary/5` and started saying `bg-primary-tint`: the fill became
// measurable and the tile came up as `primary on primary-tint — 4.38:1`, held to the
// text floor. Its entire content is one `aria-hidden` `<svg>` whose cells are
// `fill-current`, which is a graphic under 1.4.11 and clears the 3:1 that asks of it.
// The list is components-whose-whole-render-is-an-svg, and it was short only because
// nothing measurable had ever sat behind this one.
const GLYPH = /<svg\b[\s\S]*?<\/svg>|<(?:Icon|Spinner|LayoutIcon)\b[^<>]*\/>/g;

/** An element whose whole content is an icon is a graphic, not text. */
function glyphOnly(body: string): boolean {
  return body.includes('<') && body.replace(GLYPH, '').trim() === '';
}

const BG = /(?:^|\s)(?:[\w-]+:)*bg-(\S+)/g;
const INK = /(?:^|\s)(?:[\w-]+:)*text-([a-z]+(?:-[a-z]+)?)(?![\w/-])/g;
const ROLE = /(?:^|\s)type-([a-z-]+)(?![\w-])/g;

const matches = (re: RegExp, text: string): string[] =>
  [...text.matchAll(re)].map((m) => m[1] ?? '');

/** Every background this class list can paint, measurable or not — an arbitrary or
 *  translucent fill still stops the fill above it from reaching the text. */
const fillsOf = (text: string): string[] => [...new Set(matches(BG, text))];

/** The ink this class list writes in: an explicit colour, else the one its role carries. */
function inksOf(text: string): string[] {
  const explicit = matches(INK, text).filter((token) => token in HEX);
  if (explicit.length > 0) return [...new Set(explicit)];
  const roles = matches(ROLE, text)
    .map((role) => ROLE_INK[role])
    .filter((ink): ink is string => ink !== undefined && ink in HEX);
  return [...new Set(roles)];
}

const sources = import.meta.glob('/src/**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

const stylesheets = import.meta.glob('/src/**/*.css', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

// A background can also be painted in CSS: `.tb-tip-pop` is the dark tooltip the log
// terminal's colours are read on. Those names are read out of the stylesheet rather
// than listed, and count only as "something is painted here" — the fill itself is
// beyond a class scan, so it ends the reach of the fill above it and is not measured.
const cssPainted = new Set<string>();
for (const sheet of Object.values(stylesheets)) {
  for (const block of sheet.matchAll(/\.(tb-[\w-]+)[^{}]*\{([^{}]*)\}/g)) {
    if (/background(?:-color)?\s*:\s*(?!transparent)/.test(block[2] ?? '')) {
      cssPainted.add(block[1] ?? '');
    }
  }
}
const CSS_FILL = new RegExp(`(?:^|\\s)(?:${[...cssPainted].join('|')})(?![\\w-])`);

const pairings = new Map<string, string[]>();
const offenders: string[] = [];

function record(ink: string, fill: string, where: string, glyph: boolean) {
  if (ink === fill || !(ink in HEX) || !(fill in HEX)) return;
  const key = `${ink} on ${fill}`;
  pairings.set(key, [...(pairings.get(key) ?? []), where]);
  const floor = glyph ? NON_TEXT : AA;
  const measured = ratio(ink, fill);
  if (measured < floor) {
    offenders.push(`${where} ${key} — ${measured.toFixed(2)}:1, needs ${floor.toFixed(1)}`);
  }
}

const lineAt = (src: string, index: number) => src.slice(0, index).split('\n').length;

for (const [path, src] of Object.entries(sources)) {
  if (path.includes('.test.')) continue;
  const spans: { start: number; end: number; fills: string[] }[] = [];
  const floating: { at: number; inks: string[]; glyph: boolean }[] = [];
  const inATag: [number, number][] = [];
  for (let i = src.indexOf('<'); i !== -1; i = src.indexOf('<', i + 1)) {
    const tag = walkTag(src, i);
    if (tag === null) continue;
    // Props can hold whole elements (`header={<span className=… />}`); their classes
    // belong to them, not to the tag they sit in, and they get their own turn here.
    const raw = src.slice(i, tag.end + 1);
    const nested = raw.slice(1).search(/<[A-Za-z]/);
    const own = nested === -1 ? raw : raw.slice(0, nested + 1);
    inATag.push([i, i + own.length]);
    const end = elementEnd(src, tag.end, tag.name);
    const glyph = glyphOnly(
      src.slice(tag.end + 1, Math.max(tag.end + 1, end - tag.name.length - 3)),
    );
    const cs = chunks(own);
    const fills = [...new Set(cs.flatMap((c) => fillsOf(c.text)))];
    if (cs.some((c) => CSS_FILL.test(c.text))) fills.push('css');
    if (fills.length > 0) spans.push({ start: i, end, fills });
    const always = [...new Set(cs.filter((c) => c.always).flatMap((c) => fillsOf(c.text)))];
    const orphaned: string[] = [];
    for (const chunk of cs) {
      const inks = inksOf(chunk.text);
      if (inks.length === 0) continue;
      const own_ = fillsOf(chunk.text);
      // A fill and an ink meet when they share a chunk, or when either of them always
      // applies. Two different conditional branches never meet.
      const reach = new Set([...own_, ...(chunk.always ? fills : always)]);
      const where = `${path}:${lineAt(src, i + chunk.at)}`;
      for (const fill of reach) for (const ink of inks) record(ink, fill, where, glyph);
      if (fills.length === 0) orphaned.push(...inks);
    }
    if (orphaned.length > 0) floating.push({ at: i, inks: orphaned, glyph });
  }
  // Text with no fill of its own is read on the nearest painted ancestor.
  for (const { at, inks, glyph } of floating) {
    const enclosing = spans.filter((s) => s.start < at && at < s.end);
    if (enclosing.length === 0) continue;
    const nearest = enclosing.reduce((a, b) => (b.start > a.start ? b : a));
    const where = `${path}:${lineAt(src, at)}`;
    for (const fill of nearest.fills) for (const ink of inks) record(ink, fill, where, glyph);
  }
  // The class lists that never reach a tag: the tone maps and hoisted constants, which
  // is where thirty-five status pills sat on the failing rung after the first sweep.
  for (const chunk of chunks(src)) {
    if (inATag.some(([a, b]) => a <= chunk.at && chunk.at < b)) continue;
    const inks = inksOf(chunk.text);
    const where = `${path}:${lineAt(src, chunk.at)}`;
    for (const fill of fillsOf(chunk.text)) for (const ink of inks) record(ink, fill, where, false);
  }
}

// A source-reading assertion can lie in exactly one way: by reading nothing. A glob that
// resolved to nothing, or a walker that stopped finding tags, would leave both of these
// empty and every assertion below would pass on it.
test('the scan reads the tree it claims to', () => {
  expect(Object.keys(sources).length).toBeGreaterThan(100);
  expect(cssPainted.size).toBeGreaterThan(0);
  expect(pairings.size).toBeGreaterThan(40);
  expect([...pairings.keys()]).toContain('ink-subtle on white');
  expect([...pairings.keys()]).toContain('white on primary');
});

test('every text-on-fill pairing the source paints clears its floor', () => {
  // Joined rather than compared as an array: a failure has to name the file, the line
  // and the measurement, and a diff of two arrays truncates at two entries.
  expect(offenders.join('\n')).toBe('');
});

// The one kind of pairing the scan above cannot see: a fill painted in one component
// and the ink read on it written in another. `LogRow` spells the account column
// `text-term-text`; the surface under it is `bg-term`, painted by `LogTerminal` further
// down the same file. Nothing in either class list names the other, so this stays a
// hand-written line — and it is the only one left, where the table used to be
// twenty-four with no way to tell which of them were still real.
test('term.text reads on the terminal surface it is written for', () => {
  expect(ratio('term-text', 'term')).toBeGreaterThanOrEqual(AA);
});
