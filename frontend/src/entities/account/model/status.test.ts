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

  it('treats a missing session file as a permanent problem', () => {
    expect(accountHealth('session_file_missing')).toBe('fail');
  });

  it('maps an alive account with a failing link to warn, and never softens a dead key', () => {
    expect(accountHealth('alive', null)).toBe('ok');
    expect(accountHealth('alive', 'proxy_error')).toBe('warn');
    expect(accountHealth('unauthorized', 'proxy_error')).toBe('fail');
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
    expect(accountDesignStatus('session_file_missing')).toBe('banned');
    expect(accountDesignStatus('network_error')).toBe('banned');
    expect(accountDesignStatus('proxy_error')).toBe('banned');
    expect(accountDesignStatus('unknown_error')).toBe('banned');
  });
});
