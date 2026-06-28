import { describe, expect, it } from 'vitest';

import { accountHealth } from './status';

describe('accountHealth', () => {
  it('maps alive to ok', () => {
    expect(accountHealth('alive')).toBe('ok');
  });

  it('maps permanent statuses to fail', () => {
    expect(accountHealth('unauthorized')).toBe('fail');
    expect(accountHealth('session_error')).toBe('fail');
    expect(accountHealth('account_error')).toBe('fail');
  });

  it('maps everything else to warn', () => {
    expect(accountHealth('new')).toBe('warn');
    expect(accountHealth('flood_wait')).toBe('warn');
  });
});
