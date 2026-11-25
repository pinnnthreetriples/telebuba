import { describe, expect, it } from 'vitest';

import { DEFAULT_SETTINGS } from './draft';
import {
  EVEN_JOIN_DELAY,
  EVEN_REST,
  evenPause,
  hourPlan,
  joinVerdict,
  restVerdict,
} from './paceTips';

describe('hourPlan', () => {
  it('spends a fast account’s hour in a burst and the rest of it asleep', () => {
    // 60 s on average against 20 an hour: 20 sends in 20 minutes, 40 minutes asleep.
    expect(hourPlan({ min: 30, max: 90 }, 20)).toEqual({
      sent: 20,
      sends: Array.from({ length: 20 }, (_unused, index) => index * 60),
      busyMinutes: 20,
      restMinutes: 40,
      verdict: 'burst',
    });
  });

  it('calls a pause that fills the hour exactly even', () => {
    const plan = hourPlan({ min: 150, max: 210 }, 20);
    expect(plan.sent).toBe(20);
    expect(plan.restMinutes).toBe(0);
    expect(plan.verdict).toBe('even');
  });

  it('calls a pause too long to reach the limit slow', () => {
    const plan = hourPlan({ min: 300, max: 300 }, 20);
    expect(plan.sent).toBe(12);
    expect(plan.verdict).toBe('slow');
  });

  it('without a limit counts what the pause alone lets out', () => {
    const plan = hourPlan({ min: 60, max: 60 }, null);
    expect(plan.sent).toBe(60);
    expect(plan.restMinutes).toBe(0);
    expect(plan.verdict).toBe('free');
  });

  it('survives a zero pause', () => {
    expect(hourPlan({ min: 0, max: 0 }, 20).verdict).toBe('burst');
  });
});

describe('evenPause', () => {
  it('centres the range on the pause that spreads the limit over the hour', () => {
    expect(evenPause(20)).toEqual({ min: 150, max: 210 });
    expect(hourPlan(evenPause(20), 20).verdict).toBe('even');
  });

  it('is what a new campaign starts with', () => {
    // A fresh campaign must not open on the burst warning.
    expect(DEFAULT_SETTINGS.between_chats).toEqual(evenPause(DEFAULT_SETTINGS.per_hour));
  });

  it('stays even for any hourly limit', () => {
    for (const perHour of [1, 5, 7, 13, 30, 60, 120]) {
      expect(hourPlan(evenPause(perHour), perHour).verdict).toBe('even');
    }
  });
});

describe('verdicts', () => {
  it('judges the wait after a join', () => {
    expect(joinVerdict(0)).toBe('risky');
    expect(joinVerdict(30)).toBe('short');
    expect(joinVerdict(EVEN_JOIN_DELAY)).toBe('good');
    expect(joinVerdict(120)).toBe('good');
  });

  it('judges the rest between rounds by its shortest end', () => {
    expect(restVerdict({ min: 10, max: 200 })).toBe('often');
    expect(restVerdict(EVEN_REST)).toBe('good');
  });
});
