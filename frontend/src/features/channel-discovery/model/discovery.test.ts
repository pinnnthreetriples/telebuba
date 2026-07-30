import { describe, expect, it } from 'vitest';

import type { DiscoveryCandidate } from '@/shared/api';

import {
  boundsInverted,
  buildSearchRequest,
  canSubmit,
  droppedKeywords,
  EMPTY_FORM,
  formatSubscribers,
  isSelectable,
  KEYWORD_MAX_LENGTH,
  MAX_ADOPT,
  parseKeywords,
  resolveSelection,
  selectableChannels,
  type DiscoveryFormState,
} from './discovery';

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

describe('droppedKeywords', () => {
  it('names the tokens below the minimum length', () => {
    expect(droppedKeywords('crypto ab news')).toEqual(['ab']);
  });

  it('names the tokens past the cap', () => {
    const many = Array.from({ length: 12 }, (_, index) => `keyword${index}`).join(' ');
    expect(droppedKeywords(many)).toEqual(['keyword10', 'keyword11']);
  });

  it('does not count a duplicate as dropped', () => {
    expect(droppedKeywords('crypto CRYPTO')).toEqual([]);
  });

  it('ignores the blank tokens a separator run leaves behind', () => {
    expect(droppedKeywords('crypto,,  \n')).toEqual([]);
  });
});

describe('buildSearchRequest', () => {
  it('omits empty filters rather than sending nulls', () => {
    const request = buildSearchRequest(form({ keywords: 'crypto' }));
    expect(request).toEqual({ keywords: ['crypto'], use_telemetr: false });
  });

  it('carries filters and coerces subscriber bounds to integers', () => {
    const request = buildSearchRequest(
      form({
        keywords: 'crypto',
        language: 'ar',
        country: 'AE',
        minSubscribers: '500',
        maxSubscribers: '90000.7',
        seedChannel: '  @durov ',
        useTelemetr: true,
      }),
    );
    expect(request).toEqual({
      keywords: ['crypto'],
      use_telemetr: true,
      seed_channel: 'durov',
      language: 'ar',
      country: 'AE',
      members_min: 500,
      members_max: 90000,
    });
  });

  it('omits language and country when the catalogue is off', () => {
    // They reach only Telemetr.io; on the native sources they would filter nothing.
    const request = buildSearchRequest(
      form({ keywords: 'crypto', language: 'tr', country: 'TR', useTelemetr: false }),
    );
    expect(request.language).toBeUndefined();
    expect(request.country).toBeUndefined();
  });

  it('strips a pasted t.me link down to the username', () => {
    // The API caps seed_channel at 32 chars, so a full URL 422s instead of resolving.
    expect(
      buildSearchRequest(form({ keywords: 'crypto', seedChannel: 'https://t.me/durov' }))
        .seed_channel,
    ).toBe('durov');
    expect(
      buildSearchRequest(form({ keywords: 'crypto', seedChannel: 't.me/durov' })).seed_channel,
    ).toBe('durov');
  });

  it('drops unparseable or negative bounds instead of sending NaN', () => {
    const request = buildSearchRequest(
      form({ keywords: 'crypto', minSubscribers: 'abc', maxSubscribers: '-5' }),
    );
    expect(request.members_min).toBeUndefined();
    expect(request.members_max).toBeUndefined();
  });
});

describe('canSubmit', () => {
  it('rejects an empty form', () => {
    expect(canSubmit(EMPTY_FORM)).toBe(false);
  });

  it('accepts keywords alone', () => {
    expect(canSubmit(form({ keywords: 'crypto' }))).toBe(true);
  });

  it('rejects a seed channel with no keywords (the API requires keywords)', () => {
    expect(canSubmit(form({ seedChannel: '@durov' }))).toBe(false);
  });

  it('rejects keywords that are all too short', () => {
    expect(canSubmit(form({ keywords: 'ab cd' }))).toBe(false);
  });

  it('rejects subscriber bounds the wrong way round (the API refuses them)', () => {
    const inverted = form({ keywords: 'crypto', minSubscribers: '900', maxSubscribers: '100' });
    expect(boundsInverted(inverted)).toBe(true);
    expect(canSubmit(inverted)).toBe(false);
    // Equal bounds are a legitimate exact match.
    expect(
      canSubmit(form({ keywords: 'crypto', minSubscribers: '100', maxSubscribers: '100' })),
    ).toBe(true);
  });

  it('accepts a single bound', () => {
    expect(boundsInverted(form({ keywords: 'crypto', minSubscribers: '900' }))).toBe(false);
    expect(boundsInverted(form({ keywords: 'crypto', maxSubscribers: '100' }))).toBe(false);
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
