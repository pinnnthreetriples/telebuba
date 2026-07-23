import { describe, expect, it } from 'vitest';

import { accountDesignStatus, accountHealth } from './status';

describe('accountHealth', () => {
  it('maps alive to ok', () => {
    expect(accountHealth('alive')).toBe('ok');
  });

  it('maps permanent statuses to fail', () => {
    expect(accountHealth('unauthorized')).toBe('fail');
    expect(accountHealth('session_error')).toBe('fail');
    expect(accountHealth('account_error')).toBe('fail');
    expect(accountHealth('frozen')).toBe('fail');
  });

  it('maps everything else to warn', () => {
    expect(accountHealth('new')).toBe('warn');
    expect(accountHealth('flood_wait')).toBe('warn');
  });
});

describe('accountDesignStatus', () => {
  it('maps alive to the active bucket', () => {
    expect(accountDesignStatus('alive')).toBe('active');
  });

  it('maps flood_wait to the idle/spam bucket', () => {
    expect(accountDesignStatus('flood_wait')).toBe('spam');
  });

  it('maps unauthorized and new to the needs-code bucket', () => {
    expect(accountDesignStatus('unauthorized')).toBe('code');
    expect(accountDesignStatus('new')).toBe('code');
  });

  it('maps every other status to the problem/banned bucket', () => {
    expect(accountDesignStatus('session_error')).toBe('banned');
    expect(accountDesignStatus('account_error')).toBe('banned');
    expect(accountDesignStatus('frozen')).toBe('banned');
    expect(accountDesignStatus('network_error')).toBe('banned');
    expect(accountDesignStatus('proxy_error')).toBe('banned');
    expect(accountDesignStatus('unknown_error')).toBe('banned');
  });
});
