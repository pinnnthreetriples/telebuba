import { describe, expect, it } from 'vitest';

import { isWarmingConflict } from './listenerConflict';

describe('isWarmingConflict', () => {
  it.each([
    ['listener_busy_warming', true],
    ['account_running_discovery', false],
    ['listener_busy_neuroshilling', false],
  ])('classifies a 409 with message %s', (message, expected) => {
    expect(isWarmingConflict({ error: { code: 'conflict', message } })).toBe(expected);
  });

  it('ignores unrelated failures', () => {
    expect(
      isWarmingConflict({ error: { code: 'validation_error', message: 'listener_busy_warming' } }),
    ).toBe(false);
    expect(isWarmingConflict(null)).toBe(false);
  });
});
