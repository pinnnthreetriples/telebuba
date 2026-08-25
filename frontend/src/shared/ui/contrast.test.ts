import { describe, expect, test } from 'vitest';

import config from '../../../tailwind.config';

// WCAG 2.1 AA asks 4.5:1 of text under 18.66px bold / 24px regular. Every type rung
// this app has is under that, so 4.5:1 is the floor for all of them — there is no
// "large text" exception to lean on here.
const AA = 4.5;

type Ramp = Record<string, string> & { DEFAULT?: string };
const colors = config.theme?.extend?.colors as Record<string, string | Ramp>;

function hex(token: string): string {
  const [name = '', rung] = token.split('.');
  const value = colors[name];
  if (value === undefined) throw new Error(`no such colour token: ${token}`);
  if (typeof value === 'string') return value;
  const found = rung === undefined ? value.DEFAULT : value[rung];
  if (found === undefined) throw new Error(`no such colour token: ${token}`);
  return found;
}

function channel(byte: number): number {
  const c = byte / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function luminance(token: string): number {
  const value = hex(token).replace('#', '');
  const [r, g, b] = [0, 2, 4].map((i) => channel(parseInt(value.slice(i, i + 2), 16))) as [
    number,
    number,
    number,
  ];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(text: string, background: string): number {
  const a = text === 'white' ? 1 : luminance(text);
  const b = background === 'white' ? 1 : luminance(background);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

// Every text-on-fill pairing the app actually renders, read off the class lists
// rather than imagined: the greys on the three neutral surfaces, and each semantic's
// text rung on its own tint. A pairing that is not here is not painted anywhere.
const PAIRS: [string, string][] = [
  ['ink', 'white'],
  ['ink.body', 'white'],
  ['ink.muted', 'white'],
  ['ink.muted', 'surface'],
  ['ink.muted', 'canvas'],
  ['ink.muted', 'line.row'],
  ['ink.subtle', 'white'],
  ['ink.subtle', 'surface'],
  ['ink.subtle', 'canvas'],
  // WarmingBoard's pipeline panel is `primary-tint`, and its axis labels are the
  // `caption` and `meta` roles, which paint `ink.subtle`.
  ['ink.subtle', 'primary.tint'],
  ['primary', 'white'],
  ['primary.deep', 'primary.tint'],
  ['success.deep', 'success.tint'],
  ['warning.deep', 'warning.tint'],
  ['danger', 'white'],
  ['danger.deep', 'danger.tint'],
  ['white', 'primary'],
  ['white', 'primary.press'],
  ['white', 'danger'],
  ['white', 'term'],
  // The two fills that carry white text: DEFAULT green and amber are 3.37:1 and
  // 4.02:1 under it, which is why a filled badge wears the deep rung instead.
  ['white', 'success.deep'],
  ['white', 'warning.deep'],
  // The terminal's own two inks on its own surface.
  ['term.dim', 'term'],
  ['term.text', 'term'],
];

describe('every text colour the app paints clears WCAG AA on its own surface', () => {
  for (const [text, background] of PAIRS) {
    test(`${text} on ${background}`, () => {
      expect(ratio(text, background)).toBeGreaterThanOrEqual(AA);
    });
  }
});

// The pairings that used to ship: each one is a real fill/text combination that
// measured under the floor, and the reason the `deep` rungs exist. Asserting they
// still fail is what stops the fix from being quietly reverted by "restoring" the
// brighter colour — the token would go back to a value this test rejects.
test('the rungs that failed are the ones the deep rungs replaced', () => {
  expect(ratio('success', 'success.tint')).toBeLessThan(AA);
  expect(ratio('warning', 'warning.tint')).toBeLessThan(AA);
  expect(ratio('primary', 'primary.tint')).toBeLessThan(AA);
  expect(ratio('danger', 'danger.tint')).toBeLessThan(AA);
});

// The check the table above cannot make. A tone is two classes, and for most of the
// app's status pills they live in a map const rather than side by side in a
// `className` — which is where thirty-five of them stayed on the failing rung after
// the first sweep, invisible to a test that only reads the palette. This one reads
// the source and asserts the pairing itself.
const TONES = ['primary', 'success', 'warning', 'danger'] as const;
const sources = import.meta.glob('/src/**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

test('no class list names a tint fill beside its own unreadable text rung', () => {
  // A glob that resolved to nothing would pass this test on an empty list, which is
  // the one way a source-reading assertion can lie.
  expect(Object.keys(sources).length).toBeGreaterThan(100);
  const offenders: string[] = [];
  for (const [path, source] of Object.entries(sources)) {
    if (path.includes('.test.')) continue;
    source.split(/\r?\n/).forEach((line, index) => {
      for (const tone of TONES) {
        const readable = tone === 'warning' ? /text-warning-(?:deep)/ : undefined;
        const failing = new RegExp(`text-${tone}(?![\\w-])|text-warning-strong`);
        if (!line.includes(`bg-${tone}-tint`)) continue;
        if (readable?.test(line)) continue;
        if (failing.test(line)) offenders.push(`${path}:${index + 1} ${line.trim().slice(0, 70)}`);
      }
    });
  }
  expect(offenders).toEqual([]);
});
