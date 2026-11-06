import { expect, test } from 'vitest';

import {
  isCovered,
  linkAt,
  normalizeEntities,
  removeRange,
  replaceRange,
  setLink,
  toggleEntity,
} from './entities';
import type { StoryEntity } from './types';

const e = (type: StoryEntity['type'], offset: number, length: number, url?: string): StoryEntity =>
  url === undefined ? { type, offset, length } : { type, offset, length, url };

test('normalising clamps, drops empties, fuses same marks and sorts', () => {
  expect(
    normalizeEntities(
      [e('italic', 0, 3), e('bold', 4, 3), e('bold', 0, 5), e('bold', 20, 5), e('code', 2, 0)],
      10,
    ),
  ).toEqual([e('bold', 0, 7), e('italic', 0, 3)]);
});

test('links with different urls are not fused', () => {
  expect(normalizeEntities([e('text_url', 0, 2, 'a'), e('text_url', 2, 2, 'b')], 4)).toHaveLength(
    2,
  );
});

test('coverage needs the whole range inside marks of that type', () => {
  const entities = [e('bold', 0, 3), e('bold', 3, 3)];
  expect(isCovered(entities, 'bold', 1, 5)).toBe(true);
  expect(isCovered(entities, 'bold', 1, 8)).toBe(false);
  expect(isCovered(entities, 'italic', 1, 2)).toBe(false);
  expect(isCovered(entities, 'bold', 2, 2)).toBe(false);
  expect(isCovered([e('bold', 4, 2)], 'bold', 1, 5)).toBe(false);
});

test('toggling marks a range, then clears exactly that range', () => {
  const on = toggleEntity([], 'bold', 2, 6, 10);
  expect(on).toEqual([e('bold', 2, 4)]);
  expect(toggleEntity(on, 'bold', 3, 5, 10)).toEqual([e('bold', 2, 1), e('bold', 5, 1)]);
  expect(toggleEntity(on, 'bold', 2, 6, 10)).toEqual([]);
  expect(toggleEntity(on, 'bold', 4, 4, 10)).toEqual(on);
});

test('toggling over a partly marked range marks it in full', () => {
  expect(toggleEntity([e('bold', 0, 3)], 'bold', 1, 6, 10)).toEqual([e('bold', 0, 6)]);
});

test('nested marks stay independent', () => {
  const bold = toggleEntity([], 'bold', 0, 5, 5);
  const both = toggleEntity(bold, 'italic', 1, 4, 5);
  expect(both).toEqual([e('bold', 0, 5), e('italic', 1, 3)]);
});

test('a link replaces the previous link on its range and null clears it', () => {
  const first = setLink([], 0, 4, 'https://a.io', 10);
  expect(first).toEqual([e('text_url', 0, 4, 'https://a.io')]);
  expect(setLink(first, 0, 4, 'https://b.io', 10)).toEqual([e('text_url', 0, 4, 'https://b.io')]);
  expect(setLink(first, 1, 3, null, 10)).toEqual([
    e('text_url', 0, 1, 'https://a.io'),
    e('text_url', 3, 1, 'https://a.io'),
  ]);
  expect(setLink(first, 2, 2, 'https://c.io', 10)).toEqual(first);
  expect(linkAt(first, 1, 3)).toBe('https://a.io');
  expect(linkAt(first, 1, 6)).toBeNull();
  expect(linkAt(first, 2, 2)).toBe('https://a.io');
});

test('removeRange leaves other types alone', () => {
  expect(removeRange([e('bold', 0, 4), e('italic', 0, 4)], 'bold', 0, 4)).toEqual([
    e('italic', 0, 4),
  ]);
});

test('editing text carries the marks along', () => {
  const value = { text: 'hello world', entities: [e('bold', 6, 5)] };
  expect(replaceRange(value, 0, 0, 'oh ')).toEqual({
    text: 'oh hello world',
    entities: [e('bold', 9, 5)],
  });
  expect(replaceRange(value, 6, 6, '>')).toEqual({
    text: 'hello >world',
    entities: [e('bold', 7, 5)],
  });
  expect(replaceRange(value, 11, 11, '!').entities).toEqual([e('bold', 6, 5)]);
  expect(replaceRange(value, 8, 8, '--').entities).toEqual([e('bold', 6, 7)]);
  expect(replaceRange(value, 4, 8, '').entities).toEqual([e('bold', 4, 3)]);
  expect(replaceRange(value, 9, 11, '').entities).toEqual([e('bold', 6, 3)]);
  expect(replaceRange(value, 5, 11, '').entities).toEqual([]);
  expect(replaceRange(value, 4, 7, 'XY').entities).toEqual([e('bold', 6, 4)]);
});
