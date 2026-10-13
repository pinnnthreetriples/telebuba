import { expect, test } from 'vitest';

import {
  BUILT_IN_PRESETS,
  forecastSends,
  lookupOverflow,
  pacingFromLimits,
  pauseRange,
  worstZone,
  zoneOf,
} from './pacing';

test('zone boundaries follow the agreed thresholds', () => {
  expect([60, 59, 30, 29].map((value) => zoneOf('pause', value))).toEqual([
    'ok',
    'warn',
    'warn',
    'risk',
  ]);
  expect([25, 26, 40, 41].map((value) => zoneOf('messages', value))).toEqual([
    'ok',
    'warn',
    'warn',
    'risk',
  ]);
  expect([15, 16, 20, 21].map((value) => zoneOf('contacts', value))).toEqual([
    'ok',
    'warn',
    'warn',
    'risk',
  ]);
  expect([5, 6, 10, 11].map((value) => zoneOf('batch', value))).toEqual([
    'ok',
    'warn',
    'warn',
    'risk',
  ]);
  expect(worstZone(['ok', 'warn', 'ok'])).toBe('warn');
  expect(worstZone(['warn', 'risk'])).toBe('risk');
});

test('built-in presets match the operator-approved values', () => {
  expect(BUILT_IN_PRESETS).toEqual([
    { id: 'careful', values: { pause: 180, messages: 20, contacts: 12, batch: 2 } },
    { id: 'normal', values: { pause: 120, messages: 25, contacts: 15, batch: 3 } },
    { id: 'fast', values: { pause: 60, messages: 35, contacts: 20, batch: 5 } },
  ]);
});

test('the pause is jittered within ±50% of the slider', () => {
  expect(pauseRange(120)).toEqual([60, 180]);
  expect(pauseRange(15)).toEqual([8, 23]);
});

test('the forecast counts what fits today and the average wait between sends', () => {
  expect(forecastSends({ mode: 'split', recipients: 10, left: [3, 4], pause: 60 })).toEqual({
    fitting: 7,
    over: 3,
    seconds: 360,
  });
  // Each mode: every account writes to every recipient, within its own cap.
  expect(forecastSends({ mode: 'each', recipients: 2, left: [1, 25], pause: 120 })).toEqual({
    fitting: 3,
    over: 1,
    seconds: 240,
  });
  expect(forecastSends({ mode: 'split', recipients: 0, left: [5], pause: 60 }).seconds).toBe(0);
  expect(lookupOverflow(10, [3, -2, 4])).toBe(3);
  expect(lookupOverflow(2, [5])).toBe(0);
});

test('the sliders start from the saved limits, clamped to their ranges', () => {
  expect(pacingFromLimits(undefined)).toEqual(BUILT_IN_PRESETS[1]?.values);
  expect(
    pacingFromLimits({
      bulk_min_delay_seconds: 90,
      bulk_max_delay_seconds: 270,
      first_messages_per_day: null,
      contact_imports_per_day: 200,
      lookup_batch_size: 2,
    }),
  ).toEqual({ pause: 180, messages: 25, contacts: 40, batch: 2 });
});
