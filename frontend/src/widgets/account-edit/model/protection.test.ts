import { describe, expect, it } from 'vitest';

import { DEFAULT_PROTECTION, fullProtection, leftOut, youngCap } from './protection';

describe('protection model', () => {
  it('halves a young cap like the server, never below one', () => {
    expect([15, 25, 2, 1].map(youngCap)).toEqual([7, 12, 1, 1]);
  });

  it('fills the switches the saved limits left out', () => {
    expect(fullProtection({ typing: false })).toEqual({ ...DEFAULT_PROTECTION, typing: false });
  });

  it('leaves out only what the current switches would skip', () => {
    const skipped = [
      { account_id: 'a', reason: 'account_resting' as const, retry_after_seconds: 60 },
      { account_id: 'b', reason: 'account_spam_blocked' as const },
    ];
    expect([...leftOut(skipped, DEFAULT_PROTECTION).keys()]).toEqual(['a', 'b']);
    expect([...leftOut(skipped, { ...DEFAULT_PROTECTION, skip_injured: false }).keys()]).toEqual([
      'a',
    ]);
    expect([
      ...leftOut(skipped, { ...DEFAULT_PROTECTION, rest_after_limit: false }).keys(),
    ]).toEqual(['b']);
  });
});
