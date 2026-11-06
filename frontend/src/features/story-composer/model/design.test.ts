import { expect, test } from 'vitest';

import {
  createDefaultDesign,
  createSticker,
  designIsValid,
  premiumLoss,
  stickerProblems,
  toWireDesign,
} from './design';
import type { LinkSticker, StoryDesign } from './types';

const sticker = (patch: Partial<LinkSticker> = {}): LinkSticker => ({
  ...createSticker(0),
  url: 'https://example.com',
  label: 'Подробнее',
  ...patch,
});
const design = (patch: Partial<StoryDesign> = {}): StoryDesign => ({
  ...createDefaultDesign(),
  ...patch,
});

test('a default story loses nothing without Premium', () => {
  expect(premiumLoss(design())).toEqual([]);
});

test('each Premium-only feature is reported once, in a fixed order', () => {
  expect(premiumLoss(design({ period_hours: 48 }))).toEqual(['period']);
  expect(premiumLoss(design({ period_hours: 6 }))).toEqual(['period']);
  expect(premiumLoss(design({ link_stickers: [sticker()] }))).toEqual(['link_stickers']);
  expect(premiumLoss(design({ caption: 'x'.repeat(201) }))).toEqual(['long_caption']);
  expect(premiumLoss(design({ caption: 'x'.repeat(200) }))).toEqual([]);
  expect(premiumLoss(design({ entities: [{ type: 'bold', offset: 0, length: 1 }] }))).toEqual([
    'formatting',
  ]);
  expect(
    premiumLoss(
      design({
        period_hours: 12,
        link_stickers: [sticker()],
        caption: 'x'.repeat(300),
        entities: [{ type: 'code', offset: 0, length: 2 }],
      }),
    ),
  ).toEqual(['period', 'link_stickers', 'long_caption', 'formatting']);
});

test('the caption limit is a parameter and counts UTF-16 units', () => {
  expect(premiumLoss(design({ caption: '😀'.repeat(101) }))).toEqual(['long_caption']);
  expect(premiumLoss(design({ caption: 'x'.repeat(11) }), 10)).toEqual(['long_caption']);
});

test('the wire design drops sticker ids and nothing else', () => {
  const first = sticker({ x: 10, y: 20, scale: 1.5, rotation: -30, style: 'dark' });
  const wire = toWireDesign(design({ pinned: true, link_stickers: [first] }));

  expect(wire.link_stickers).toEqual([
    {
      url: 'https://example.com',
      label: 'Подробнее',
      style: 'dark',
      x: 10,
      y: 20,
      scale: 1.5,
      rotation: -30,
    },
  ]);
  expect(wire.link_stickers[0]).not.toHaveProperty('id');
  expect(wire.pinned).toBe(true);
  expect(wire.period_hours).toBe(24);
});

test('a fourth sticker never reaches the wire', () => {
  const four = [0, 1, 2, 3].map(() => sticker());
  expect(toWireDesign(design({ link_stickers: four })).link_stickers).toHaveLength(3);
});

test('new stickers get unique ids and fan out downwards', () => {
  const a = createSticker(0);
  const b = createSticker(1);
  const c = createSticker(9);
  expect(a.id).not.toBe(b.id);
  expect(b.y).toBeGreaterThan(a.y);
  expect(c.y).toBeLessThanOrEqual(85);
});

test('sticker problems and design validity follow url and label rules', () => {
  expect(stickerProblems(sticker())).toEqual({ url: null, label: null });
  expect(stickerProblems(sticker({ url: 'nope' })).url).toBe('invalid');
  expect(stickerProblems(sticker({ label: '🔥' })).label).toBe('emoji');
  expect(designIsValid(design())).toBe(true);
  expect(designIsValid(design({ link_stickers: [sticker()] }))).toBe(true);
  expect(designIsValid(design({ link_stickers: [sticker(), sticker({ label: '' })] }))).toBe(false);
});
