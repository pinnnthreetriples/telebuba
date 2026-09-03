import { describe, expect, it } from 'vitest';

import type { DiscoveryCandidate } from '@/shared/api';

import {
  boundInvalid,
  boundsInverted,
  buildSearchRequest,
  canSubmit,
  splitKeywords,
  EMPTY_FORM,
  formatSubscribers,
  isPrivateRef,
  isSelectable,
  KEYWORD_MAX_LENGTH,
  MAX_ADOPT,
  parseKeywords,
  resolveSelection,
  selectableChannels,
  type DiscoveryFormState,
} from './discovery';
import { LIMIT_DEFAULT, MAX_SEARCH_ACCOUNTS } from './filters';

function form(overrides: Partial<DiscoveryFormState> = {}): DiscoveryFormState {
  return { ...EMPTY_FORM, ...overrides };
}

function candidate(overrides: Partial<DiscoveryCandidate> = {}): DiscoveryCandidate {
  return {
    channel: 'alpha',
    source: 'telegram_search',
    qualification: 'pending',
    ...overrides,
  };
}

describe('parseKeywords', () => {
  it('splits on commas, spaces and newlines', () => {
    expect(parseKeywords('crypto, trading\nnews')).toEqual(['crypto', 'trading', 'news']);
  });

  it('drops tokens below the Telegram minimum', () => {
    expect(parseKeywords('abc crypto ab')).toEqual(['crypto']);
  });

  it('drops tokens above the API maximum length', () => {
    // A pasted t.me URL is one long token; the API refuses it with a 422.
    const longest = 'k'.repeat(KEYWORD_MAX_LENGTH);
    expect(parseKeywords(longest)).toEqual([longest]);
    expect(parseKeywords(`${longest}k`)).toEqual([]);
  });

  it('strips @ noise and dedupes case-insensitively', () => {
    expect(parseKeywords('@Crypto crypto CRYPTO')).toEqual(['Crypto']);
  });

  it('caps the list at the API maximum', () => {
    const many = Array.from({ length: 15 }, (_, index) => `keyword${index}`).join(' ');
    expect(parseKeywords(many)).toHaveLength(10);
  });

  it('returns an empty list for blank input', () => {
    expect(parseKeywords('')).toEqual([]);
    expect(parseKeywords('   ,  \n ')).toEqual([]);
  });

  it('measures keywords in code points, like the backend does', () => {
    // Four UTF-16 units, two code points: the server counts two and 422s.
    expect(parseKeywords('🚀🚀')).toEqual([]);
    // Eight code points inside the 64 limit, sixteen UTF-16 units — dropping it here
    // would refuse a keyword the API accepts.
    expect(parseKeywords('🚀🚀🚀🚀🚀🚀🚀🚀')).toEqual(['🚀🚀🚀🚀🚀🚀🚀🚀']);
  });
});

describe('splitKeywords dropped tokens', () => {
  it('names the tokens below the minimum length', () => {
    expect(splitKeywords('crypto ab news').dropped).toEqual(['ab']);
  });

  it('names the tokens past the cap', () => {
    const many = Array.from({ length: 12 }, (_, index) => `keyword${index}`).join(' ');
    expect(splitKeywords(many).dropped).toEqual(['keyword10', 'keyword11']);
  });

  it('does not count a duplicate as dropped', () => {
    expect(splitKeywords('crypto CRYPTO').dropped).toEqual([]);
  });

  it('ignores the blank tokens a separator run leaves behind', () => {
    expect(splitKeywords('crypto,,  \n').dropped).toEqual([]);
  });
});

const ACCOUNTS = ['acc-1'];

// What every request carries whatever the form says: the server's defaults may drift.
const DEFAULTS = {
  kind: 'channels',
  category: 'any',
  language: 'any',
  comments: 'any',
  access: 'any',
  hide_seen: true,
  limit: LIMIT_DEFAULT,
  account_ids: ACCOUNTS,
};

describe('buildSearchRequest', () => {
  it('omits empty filters rather than sending nulls, but always sends the defaults', () => {
    const request = buildSearchRequest(form({ keywords: 'crypto' }), ACCOUNTS);
    expect(request).toEqual({ keywords: ['crypto'], ...DEFAULTS });
  });

  it('carries the seed and the subscriber bounds as integers', () => {
    const request = buildSearchRequest(
      form({
        keywords: 'crypto',
        minSubscribers: '500',
        maxSubscribers: ' 90000 ',
        seedChannel: '  @durov ',
      }),
      ACCOUNTS,
    );
    expect(request).toEqual({
      keywords: ['crypto'],
      seed_channel: 'durov',
      members_min: 500,
      members_max: 90000,
      ...DEFAULTS,
    });
  });

  it('strips a pasted t.me link down to the username', () => {
    // The API caps seed_channel at 32 chars, so a full URL 422s instead of resolving.
    expect(
      buildSearchRequest(form({ keywords: 'crypto', seedChannel: 'https://t.me/durov' }), ACCOUNTS)
        .seed_channel,
    ).toBe('durov');
    expect(
      buildSearchRequest(form({ keywords: 'crypto', seedChannel: 't.me/durov' }), ACCOUNTS)
        .seed_channel,
    ).toBe('durov');
  });

  it('drops unparseable or negative bounds instead of sending NaN', () => {
    const request = buildSearchRequest(
      form({ keywords: 'crypto', minSubscribers: 'abc', maxSubscribers: '-5' }),
      ACCOUNTS,
    );
    expect(request.members_min).toBeUndefined();
    expect(request.members_max).toBeUndefined();
  });

  it('takes whole numbers only — no exponent, no decimals', () => {
    // `Number('1e3')` is 1000 and `Number('90.7')` floors to 90: both would have sent a
    // bound the operator never typed.
    for (const raw of ['1e3', '90.7', '+5', '0x10']) {
      const request = buildSearchRequest(
        form({ keywords: 'crypto', minSubscribers: raw, maxSubscribers: raw }),
        ACCOUNTS,
      );
      expect(request.members_min, raw).toBeUndefined();
      expect(request.members_max, raw).toBeUndefined();
      expect(boundInvalid(raw), raw).toBe(true);
    }
    expect(boundInvalid('')).toBe(false);
    expect(boundInvalid(' 12 ')).toBe(false);
  });

  it('carries the picked filters, the limit and the accounts', () => {
    const request = buildSearchRequest(
      form({
        category: 'crypto',
        kind: 'all',
        language: 'ru',
        comments: 'on',
        access: 'join_request',
        hideSeen: false,
        limit: '50',
      }),
      ['acc-1', 'acc-2'],
    );
    expect(request).toEqual({
      keywords: [],
      kind: 'all',
      category: 'crypto',
      language: 'ru',
      comments: 'on',
      access: 'join_request',
      hide_seen: false,
      limit: 50,
      account_ids: ['acc-1', 'acc-2'],
    });
  });

  it('neutralises the picks the server refuses for groups', () => {
    // Groups have no comments verdict and never come by subscription: a pick left over
    // from 'channels' would 422 the whole request.
    const request = buildSearchRequest(
      form({ keywords: 'crypto', kind: 'groups', comments: 'on', access: 'subscription' }),
      ACCOUNTS,
    );
    expect(request.comments).toBe('any');
    expect(request.access).toBe('any');
    // Any other access filter is legitimate for groups.
    expect(
      buildSearchRequest(form({ keywords: 'crypto', kind: 'groups', access: 'open' }), ACCOUNTS)
        .access,
    ).toBe('open');
  });

  it('falls back to the default limit when the field is invalid', () => {
    // canSubmit blocks the form first; this only keeps the wire request well-formed.
    expect(buildSearchRequest(form({ keywords: 'crypto', limit: 'abc' }), ACCOUNTS).limit).toBe(
      LIMIT_DEFAULT,
    );
  });
});

describe('canSubmit', () => {
  it('rejects an empty form', () => {
    expect(canSubmit(EMPTY_FORM, ACCOUNTS)).toBe(false);
  });

  it('accepts keywords alone', () => {
    expect(canSubmit(form({ keywords: 'crypto' }), ACCOUNTS)).toBe(true);
  });

  it('accepts a category with no keywords (its word bundle is enough to search on)', () => {
    expect(canSubmit(form({ category: 'crypto' }), ACCOUNTS)).toBe(true);
  });

  it('rejects a seed channel with no keywords or category', () => {
    expect(canSubmit(form({ seedChannel: '@durov' }), ACCOUNTS)).toBe(false);
  });

  it('rejects keywords that are all too short', () => {
    expect(canSubmit(form({ keywords: 'ab cd' }), ACCOUNTS)).toBe(false);
  });

  it('rejects a form with no account to search with', () => {
    expect(canSubmit(form({ keywords: 'crypto' }), [])).toBe(false);
  });

  it('rejects more accounts than the server accepts', () => {
    const ids = (count: number) =>
      Array.from({ length: count }, (_, index) => `acc-${String(index)}`);
    expect(canSubmit(form({ keywords: 'crypto' }), ids(MAX_SEARCH_ACCOUNTS))).toBe(true);
    expect(canSubmit(form({ keywords: 'crypto' }), ids(MAX_SEARCH_ACCOUNTS + 1))).toBe(false);
  });

  it('rejects an invalid result limit', () => {
    expect(canSubmit(form({ keywords: 'crypto', limit: '600' }), ACCOUNTS)).toBe(false);
    expect(canSubmit(form({ keywords: 'crypto', limit: '' }), ACCOUNTS)).toBe(true);
  });

  it('rejects subscriber bounds the wrong way round (the API refuses them)', () => {
    const inverted = form({ keywords: 'crypto', minSubscribers: '900', maxSubscribers: '100' });
    expect(boundsInverted(inverted)).toBe(true);
    expect(canSubmit(inverted, ACCOUNTS)).toBe(false);
    // Equal bounds are a legitimate exact match.
    expect(
      canSubmit(
        form({ keywords: 'crypto', minSubscribers: '100', maxSubscribers: '100' }),
        ACCOUNTS,
      ),
    ).toBe(true);
  });

  it('accepts a single bound', () => {
    expect(boundsInverted(form({ keywords: 'crypto', minSubscribers: '900' }))).toBe(false);
    expect(boundsInverted(form({ keywords: 'crypto', maxSubscribers: '100' }))).toBe(false);
  });

  it('rejects a typed bound that is not a whole number rather than searching without it', () => {
    expect(canSubmit(form({ keywords: 'crypto', minSubscribers: '1e3' }), ACCOUNTS)).toBe(false);
    expect(canSubmit(form({ keywords: 'crypto', maxSubscribers: 'abc' }), ACCOUNTS)).toBe(false);
    expect(canSubmit(form({ keywords: 'crypto', maxSubscribers: '10' }), ACCOUNTS)).toBe(true);
  });
});

describe('isPrivateRef', () => {
  it('recognises the backend PRIVATE_PREFIX and nothing that merely starts with id', () => {
    expect(isPrivateRef('id:123456')).toBe(true);
    expect(isPrivateRef('identity')).toBe(false);
    expect(isPrivateRef('durov')).toBe(false);
  });
});

describe('isSelectable', () => {
  it('accepts a free channel whose comments are on or still unknown', () => {
    expect(isSelectable(candidate({ qualification: 'comments_on' }))).toBe(true);
    expect(isSelectable(candidate({ qualification: 'pending' }))).toBe(true);
    expect(isSelectable(candidate({ qualification: 'unknown' }))).toBe(true);
  });

  it('rejects a channel with comments off', () => {
    expect(isSelectable(candidate({ qualification: 'comments_off' }))).toBe(false);
  });

  it('rejects a channel already in this or another campaign', () => {
    expect(isSelectable(candidate({ in_campaign: true }))).toBe(false);
    expect(isSelectable(candidate({ taken_by_other_campaign: true }))).toBe(false);
  });

  it('rejects a group and a subscription-gated channel (the adopt answers not_adoptable)', () => {
    expect(isSelectable(candidate({ kind: 'group', qualification: 'comments_on' }))).toBe(false);
    expect(isSelectable(candidate({ access: 'subscription' }))).toBe(false);
    expect(isSelectable(candidate({ kind: 'channel', access: 'join_request' }))).toBe(true);
  });

  it('rejects a private row by its id: name even when the access badge is gone', () => {
    // After a restart the in-memory verdict is lost and `access` comes back null.
    expect(isSelectable(candidate({ channel: 'id:123456', qualification: 'comments_on' }))).toBe(
      false,
    );
  });
});

describe('selection helpers', () => {
  const candidates = [
    candidate({ channel: 'good', qualification: 'comments_on' }),
    candidate({ channel: 'closed', qualification: 'comments_off' }),
    candidate({ channel: 'taken', taken_by_other_campaign: true }),
  ];

  it('lists only eligible channels', () => {
    expect(selectableChannels(candidates)).toEqual(['good']);
  });

  it('re-intersects picks with the latest eligible set', () => {
    // 'closed' flipped to comments_off after the operator ticked it.
    const selected = new Set(['good', 'closed']);
    expect(resolveSelection(selected, candidates)).toEqual(['good']);
  });

  it('caps the batch at the API maximum', () => {
    const many = Array.from({ length: MAX_ADOPT + 10 }, (_, index) =>
      candidate({ channel: `chan_${index}`, qualification: 'comments_on' }),
    );
    const selected = new Set(many.map((item) => item.channel));
    expect(MAX_ADOPT).toBe(500); // matches the API's MAX_ADOPT_CHANNELS
    expect(resolveSelection(selected, many)).toHaveLength(MAX_ADOPT);
  });
});

describe('formatSubscribers', () => {
  it('renders an em dash when the count is unknown', () => {
    expect(formatSubscribers(null, 'en')).toBe('—');
    expect(formatSubscribers(undefined, 'en')).toBe('—');
  });

  it('renders compact notation', () => {
    expect(formatSubscribers(12345, 'en')).toBe('12.3K');
    expect(formatSubscribers(900, 'en')).toBe('900');
    expect(formatSubscribers(1_500_000, 'en')).toBe('1.5M');
  });
});
