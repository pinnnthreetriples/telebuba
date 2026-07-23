import { describe, expect, it } from 'vitest';

import { accountDisplayName } from './displayName';

describe('accountDisplayName', () => {
  it('joins first and last name when present', () => {
    expect(accountDisplayName({ first_name: 'Vika', last_name: 'Ix', account_id: 'a1' })).toBe(
      'Vika Ix',
    );
  });

  it('uses a lone first name', () => {
    expect(accountDisplayName({ first_name: 'Vika', account_id: 'a1' })).toBe('Vika');
  });

  it('falls back to the phone when there is no name', () => {
    expect(accountDisplayName({ phone: '+79990000001', account_id: 'a1' })).toBe('+79990000001');
  });

  it('falls back to the account id when there is no name or phone', () => {
    expect(accountDisplayName({ account_id: 'a1' })).toBe('a1');
  });
});
