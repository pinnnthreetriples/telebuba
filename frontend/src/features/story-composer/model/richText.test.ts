import { expect, test } from 'vitest';

import { offsetOfPoint, pointAtOffset, readCaption, renderCaption, sameCaption } from './richText';
import type { CaptionValue, StoryEntity } from './types';

const e = (type: StoryEntity['type'], offset: number, length: number, url?: string): StoryEntity =>
  url === undefined ? { type, offset, length } : { type, offset, length, url };

function roundTrip(value: CaptionValue): CaptionValue {
  const root = document.createElement('div');
  renderCaption(root, value);
  return readCaption(root);
}

test('plain text round-trips', () => {
  expect(roundTrip({ text: 'hello', entities: [] })).toEqual({ text: 'hello', entities: [] });
});

test('every mark type round-trips with its offsets', () => {
  const value: CaptionValue = {
    text: 'a b c d e f g',
    entities: [
      e('bold', 0, 1),
      e('italic', 2, 1),
      e('underline', 4, 1),
      e('strike', 6, 1),
      e('spoiler', 8, 1),
      e('code', 10, 1),
      e('text_url', 12, 1, 'https://example.com'),
    ],
  };
  expect(roundTrip(value)).toEqual(value);
});

test('emoji count as two UTF-16 units on both sides of a mark', () => {
  const value: CaptionValue = { text: '😀 ok 😀', entities: [e('bold', 3, 2)] };
  expect(value.text.length).toBe(8);
  expect(roundTrip(value)).toEqual(value);
  expect(roundTrip({ text: '😀😀', entities: [e('italic', 0, 2)] }).entities).toEqual([
    e('italic', 0, 2),
  ]);
});

test('nested and overlapping marks survive', () => {
  const nested: CaptionValue = { text: 'abcdef', entities: [e('bold', 0, 6), e('italic', 2, 2)] };
  expect(roundTrip(nested)).toEqual(nested);
  const overlapping: CaptionValue = {
    text: 'abcdef',
    entities: [e('bold', 0, 4), e('italic', 2, 4)],
  };
  expect(roundTrip(overlapping)).toEqual(overlapping);
  const withLink: CaptionValue = {
    text: 'abcdef',
    entities: [e('text_url', 1, 4, 'https://a.io'), e('bold', 2, 2)],
  };
  expect(roundTrip(withLink)).toEqual(withLink);
});

test('newlines round-trip, including a trailing one', () => {
  expect(roundTrip({ text: 'a\nb', entities: [e('bold', 1, 2)] })).toEqual({
    text: 'a\nb',
    entities: [e('bold', 1, 2)],
  });
  const root = document.createElement('div');
  renderCaption(root, { text: 'a\n', entities: [] });
  expect(root.querySelector('br[data-sentinel]')).not.toBeNull();
  expect(readCaption(root).text).toBe('a\n');
});

test('marks split across several elements are read as one entity', () => {
  const root = document.createElement('div');
  root.innerHTML = '<b>ab</b><strong>cd</strong>e';
  expect(readCaption(root)).toEqual({ text: 'abcde', entities: [e('bold', 0, 4)] });
});

test('browser-made structure is read: br, divs, legacy tags and href links', () => {
  const root = document.createElement('div');
  root.innerHTML =
    'one<br>two<div>three</div><div><br></div><p>four</p><i>x</i><strike>y</strike><del>z</del>' +
    '<a href="https://a.io">l</a><a>nolink</a>';
  const read = readCaption(root);
  expect(read.text).toBe('one\ntwo\nthree\n\nfour' + 'xyz' + 'lnolink');
  expect(read.entities.map((entity) => entity.type)).toEqual(['italic', 'strike', 'text_url']);
  expect(read.entities[2]).toEqual(e('text_url', 22, 1, 'https://a.io'));
});

test('caret offsets and DOM points agree, through marks and emoji', () => {
  const root = document.createElement('div');
  renderCaption(root, { text: 'ab😀cd', entities: [e('bold', 1, 4)] });

  const bold = root.querySelector('strong');
  const text = bold?.firstChild;
  expect(text).toBeTruthy();
  expect(offsetOfPoint(root, text as Node, 3)).toBe(4);
  expect(offsetOfPoint(root, root, 0)).toBe(0);
  expect(offsetOfPoint(root, root, root.childNodes.length)).toBe(6);
  expect(offsetOfPoint(root, document.createElement('p'), 0)).toBeNull();

  const point = pointAtOffset(root, 4);
  expect(offsetOfPoint(root, point.node, point.offset)).toBe(4);
  expect(pointAtOffset(root, 0).offset).toBe(0);
  expect(pointAtOffset(root, 99).node).not.toBe(root);
});

test('points before a <br> and in an empty root resolve', () => {
  const root = document.createElement('div');
  root.innerHTML = 'a<br>b';
  const before = pointAtOffset(root, 1);
  expect(offsetOfPoint(root, before.node, before.offset)).toBe(1);
  const after = pointAtOffset(root, 2);
  expect(offsetOfPoint(root, after.node, after.offset)).toBe(2);
  const empty = document.createElement('div');
  expect(pointAtOffset(empty, 0)).toEqual({ node: empty, offset: 0 });
});

test('sameCaption compares text and every entity field', () => {
  const a: CaptionValue = { text: 'ab', entities: [e('bold', 0, 1)] };
  expect(sameCaption(a, { text: 'ab', entities: [e('bold', 0, 1)] })).toBe(true);
  expect(sameCaption(a, { text: 'ab', entities: [] })).toBe(false);
  expect(sameCaption(a, { text: 'ac', entities: a.entities })).toBe(false);
  expect(sameCaption(a, { text: 'ab', entities: [e('bold', 0, 2)] })).toBe(false);
  expect(sameCaption(a, { text: 'ab', entities: [e('italic', 0, 1)] })).toBe(false);
  expect(
    sameCaption(
      { text: 'a', entities: [e('text_url', 0, 1, 'x')] },
      { text: 'a', entities: [e('text_url', 0, 1, 'y')] },
    ),
  ).toBe(false);
});
