import { describe, expect, it } from 'vitest';

import type { DiscoveryAccountOption } from '@/shared/api';

import {
  ACCESS,
  CATEGORIES,
  COMMENTS,
  defaultAccountIds,
  effectiveAccountIds,
  eligibleAccountIds,
  groupsDisable,
  KINDS,
  LANGUAGES,
  LIMIT_DEFAULT,
  LIMIT_MAX,
  LIMIT_MIN,
  limitInvalid,
  parseLimit,
} from './filters';

function account(overrides: Partial<DiscoveryAccountOption> = {}): DiscoveryAccountOption {
  return { account_id: 'a1', name: 'Alpha', premium: false, busy_reason: null, ...overrides };
}

describe('filter tuples', () => {
  it('put the wildcard first and list every code once', () => {
    expect(KINDS).toEqual(['all', 'channels', 'groups']);
    expect(CATEGORIES[0]).toBe('any');
    expect(CATEGORIES).toHaveLength(26);
    expect(new Set(CATEGORIES).size).toBe(CATEGORIES.length);
    expect(LANGUAGES).toEqual(['any', 'ru', 'en', 'uk', 'other']);
    expect(COMMENTS).toEqual(['any', 'on', 'off']);
    expect(ACCESS).toEqual(['any', 'open', 'join_request', 'subscription']);
  });
});

describe('parseLimit', () => {
  it('reads an empty field as the server default', () => {
    expect(parseLimit('')).toBe(LIMIT_DEFAULT);
    expect(parseLimit('   ')).toBe(LIMIT_DEFAULT);
    expect(limitInvalid('')).toBe(false);
  });

  it('accepts an integer within the bounds', () => {
    expect(parseLimit(' 50 ')).toBe(50);
    expect(parseLimit(String(LIMIT_MIN))).toBe(LIMIT_MIN);
    expect(parseLimit(String(LIMIT_MAX))).toBe(LIMIT_MAX);
  });

  it('refuses rather than clamps anything else', () => {
    // A typed 600 silently becoming 500 is a request the operator did not make.
    expect(parseLimit(String(LIMIT_MAX + 1))).toBeUndefined();
    expect(parseLimit('0')).toBeUndefined();
    expect(parseLimit('-5')).toBeUndefined();
    expect(parseLimit('12.5')).toBeUndefined();
    expect(parseLimit('abc')).toBeUndefined();
    expect(limitInvalid('abc')).toBe(true);
  });
});

describe('account picking', () => {
  const accounts = [
    account({ account_id: 'busy', busy_reason: 'account_busy' }),
    account({ account_id: 'plain' }),
    account({ account_id: 'gold', premium: true }),
    account({ account_id: 'cooling', premium: true, busy_reason: 'account_cooling' }),
    account({ account_id: 'silver', premium: true }),
  ];

  it('lists only the accounts nothing else is using', () => {
    expect(eligibleAccountIds(accounts)).toEqual(['plain', 'gold', 'silver']);
  });

  it('defaults to the premium accounts when there is at least one', () => {
    expect(defaultAccountIds(accounts)).toEqual(['gold', 'silver']);
  });

  it('defaults to every eligible account when none is premium', () => {
    const plain = [
      account({ account_id: 'one' }),
      account({ account_id: 'two', premium: null }),
      account({ account_id: 'busy', busy_reason: 'no_session' }),
    ];
    expect(defaultAccountIds(plain)).toEqual(['one', 'two']);
    expect(defaultAccountIds([])).toEqual([]);
  });

  it('resolves an untouched picker to the default', () => {
    expect(effectiveAccountIds(null, accounts)).toEqual(['gold', 'silver']);
  });

  it('re-intersects a pick with the latest eligible set, in list order', () => {
    // 'busy' got taken by warming after the operator ticked it; 'ghost' left the fleet.
    expect(effectiveAccountIds(['silver', 'busy', 'plain', 'ghost'], accounts)).toEqual([
      'plain',
      'silver',
    ]);
    expect(effectiveAccountIds([], accounts)).toEqual([]);
  });
});

describe('groupsDisable', () => {
  it('disables the comments filter and subscription access for groups only', () => {
    expect(groupsDisable('groups')).toEqual({ comments: true, subscription: true });
    expect(groupsDisable('channels')).toEqual({ comments: false, subscription: false });
    expect(groupsDisable('all')).toEqual({ comments: false, subscription: false });
  });
});
