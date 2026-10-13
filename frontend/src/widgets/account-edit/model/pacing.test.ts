import { expect, test } from 'vitest';

import {
  BUILT_IN_PRESETS,
  forecastSends,
  lookupOverflow,
  pacingFromLimits,
  pauseRange,
  windowMinutes,
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

test('the forecast spreads sends that do not fit today over working days', () => {
  const base = { cap: 4, pause: 60, concurrency: 2, windowMinutes: 660 } as const;
  // Five each; the first account has 3 left today, the second 4: both need a second day.
  expect(forecastSends({ ...base, mode: 'split', recipients: 10, left: [3, 4] }).days).toBe(2);
  // Each mode: every account writes to every recipient within its own cap.
  expect(forecastSends({ ...base, cap: 25, mode: 'each', recipients: 2, left: [1, 25] }).days).toBe(
    2,
  );
  // Fits today: two accounts in parallel, four sends a minute apart.
  expect(forecastSends({ ...base, cap: 25, mode: 'split', recipients: 4, left: [25, 25] })).toEqual(
    { sends: 4, days: 1, seconds: 60 },
  );
  // No cap, but a 10-minute pause fits only 6 sends into a one-hour window.
  expect(
    forecastSends({
      mode: 'split',
      recipients: 13,
      left: [1000],
      cap: null,
      pause: 600,
      concurrency: 1,
      windowMinutes: 60,
    }).days,
  ).toBe(3);
  expect(forecastSends({ ...base, mode: 'split', recipients: 0, left: [5] }).days).toBe(0);
  expect(windowMinutes('09:00', '20:00')).toBe(660);
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
