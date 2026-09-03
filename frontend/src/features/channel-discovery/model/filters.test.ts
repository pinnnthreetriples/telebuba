import { describe, expect, it } from 'vitest';

import type { DiscoveryAccountOption } from '@/shared/api';

import { EMPTY_FORM } from './discovery';
import {
  ACCESS,
  CATEGORIES,
  COMMENTS,
  effectiveAccountIds,
  eligibleAccountIds,
  KINDS,
  LANGUAGES,
  LIMIT_DEFAULT,
  LIMIT_MAX,
  LIMIT_MIN,
  MAX_SEARCH_ACCOUNTS,
  normalizeForKind,
  parseLimit,
  SEED_MAX_LENGTH,
  seedInvalid,
  stripSeed,
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

  it('defaults to the eligible accounts, premium first, then the rest in list order', () => {
    expect(effectiveAccountIds(null, accounts)).toEqual(['gold', 'silver', 'plain']);
    expect(effectiveAccountIds(null, [account({ premium: null })])).toEqual(['a1']);
    expect(effectiveAccountIds(null, [])).toEqual([]);
  });

  it('caps the default at what the server accepts', () => {
    // Two premium at the END of a long list: they lead the pick, the plain ones fill it.
    const many = Array.from({ length: MAX_SEARCH_ACCOUNTS + 2 }, (_, index) =>
      account({ account_id: `p${String(index)}` }),
    ).concat([
      account({ account_id: 'gold', premium: true }),
      account({ account_id: 'silver', premium: true }),
    ]);
    const picked = effectiveAccountIds(null, many);
    expect(picked).toHaveLength(MAX_SEARCH_ACCOUNTS);
    expect(picked.slice(0, 2)).toEqual(['gold', 'silver']);
    expect(picked[2]).toBe('p0');
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

describe('normalizeForKind', () => {
  it('drops the picks the server refuses for groups', () => {
    // Groups have no comments verdict and never come by subscription.
    const groups = { ...EMPTY_FORM, kind: 'groups' as const, comments: 'on' as const };
    expect(normalizeForKind({ ...groups, access: 'subscription' })).toMatchObject({
      kind: 'groups',
      comments: 'any',
      access: 'any',
    });
    // Any other access filter is legitimate for groups.
    expect(normalizeForKind({ ...groups, access: 'open' }).access).toBe('open');
  });

  it('leaves channels and "all" untouched', () => {
    const form = { ...EMPTY_FORM, comments: 'on' as const, access: 'subscription' as const };
    expect(normalizeForKind(form)).toBe(form);
    expect(normalizeForKind({ ...form, kind: 'all' })).toMatchObject({
      comments: 'on',
      access: 'subscription',
    });
  });
});

describe('seedInvalid', () => {
  it('mirrors the API cap on seed_channel, in code points', () => {
    expect(SEED_MAX_LENGTH).toBe(32); // seed_channel.maxLength in openapi.json
    expect(seedInvalid('k'.repeat(SEED_MAX_LENGTH))).toBe(false);
    expect(seedInvalid(`k${'k'.repeat(SEED_MAX_LENGTH)}`)).toBe(true);
    // Measured after the strip: the link prefix does not count against the cap.
    expect(seedInvalid(`https://t.me/${'k'.repeat(SEED_MAX_LENGTH)}`)).toBe(false);
    expect(seedInvalid('')).toBe(false);
  });

  it('refuses a path that is not a handle', () => {
    // An invite or a post link resolves to nothing server-side (`seed_unusable`).
    expect(seedInvalid('t.me/joinchat/AbCdEf')).toBe(true);
    expect(seedInvalid('https://t.me/durov/123')).toBe(true);
    expect(seedInvalid('t.me/s/durov')).toBe(false);
  });
});

describe('stripSeed', () => {
  it('drops the t.me prefixes and the @ down to the handle', () => {
    expect(stripSeed(' @durov ')).toBe('durov');
    expect(stripSeed('https://t.me/durov')).toBe('durov');
    // The web-preview form of a channel link.
    expect(stripSeed('https://t.me/s/durov')).toBe('durov');
    expect(stripSeed('t.me/s/durov')).toBe('durov');
  });
});
