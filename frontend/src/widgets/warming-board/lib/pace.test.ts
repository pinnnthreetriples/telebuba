import { expect, test } from 'vitest';

import type { WarmingTuning } from '@/shared/api';

import { capsValid, draftCaps, paceRows } from './pace';

const TUNING: WarmingTuning = {
  preset: 'normal',
  custom: null,
  daily_caps: { intro: 15, settling: 15, warming: 20, active: 40, warmed: 80 },
  pause_factor: 1,
  dm_factor: 1,
  cadence: [
    { persona: 'calm', phase: 'intro', visits_min: 2, visits_max: 2, actions_per_visit: 5 },
    { persona: 'active', phase: 'intro', visits_min: 5, visits_max: 6, actions_per_visit: 3 },
  ],
  warnings: [],
};

test('rows carry each phase limit and a visits span per persona', () => {
  const [intro, settling] = paceRows(TUNING);

  expect(intro).toEqual({
    phase: 'intro',
    cap: 15,
    visits: { calm: '2', normal: '—', active: '5–6' },
  });
  expect(settling?.cap).toBe(15);
});

test('the custom draft starts from the saved limits, else the ones in force', () => {
  const custom = { intro: 5, settling: 6, warming: 7, active: 8, warmed: 9 };

  expect(draftCaps(TUNING)).toEqual(TUNING.daily_caps);
  expect(draftCaps({ ...TUNING, custom: { daily_caps: custom } })).toEqual(custom);
});

test('custom limits stay whole, within 1..80 and never drop', () => {
  const ok = { intro: 1, settling: 1, warming: 20, active: 40, warmed: 80 };

  expect(capsValid(ok)).toBe(true);
  expect(capsValid({ ...ok, warmed: 81 })).toBe(false);
  expect(capsValid({ ...ok, intro: 0, settling: 0 })).toBe(false);
  expect(capsValid({ ...ok, active: 19 })).toBe(false);
  expect(capsValid({ ...ok, warming: 2.5 })).toBe(false);
});
