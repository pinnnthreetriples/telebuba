// Pure discovery logic — no React, no server calls. Lives in a .ts module because
// react-refresh/only-export-components forbids non-component exports from .tsx.
import type { DiscoveryCandidate, DiscoverySearchRequest } from '@/shared/api';

import {
  LIMIT_DEFAULT,
  MAX_SEARCH_ACCOUNTS,
  normalizeForKind,
  parseLimit,
  seedInvalid,
  stripSeed,
  type DiscoveryAccess,
  type DiscoveryCategory,
  type DiscoveryComments,
  type DiscoveryKind,
  type DiscoveryLanguage,
} from './filters';

// Telegram rejects global searches shorter than this; the backend validates it too,
// but the form should not let the operator submit a request it will refuse.
export const KEYWORD_MIN_LENGTH = 4;
export const KEYWORD_MAX_LENGTH = 64;
export const MAX_KEYWORDS = 10;
// Matches the API's MAX_ADOPT_CHANNELS, itself the ceiling of discovery_max_candidates:
// everything a run can show must be adoptable in one go.
export const MAX_ADOPT = 500;

export type DiscoveryFormState = {
  keywords: string;
  seedChannel: string;
  minSubscribers: string;
  maxSubscribers: string;
  kind: DiscoveryKind;
  category: DiscoveryCategory;
  language: DiscoveryLanguage;
  comments: DiscoveryComments;
  access: DiscoveryAccess;
  hideSeen: boolean;
  // '' = the server default (LIMIT_DEFAULT).
  limit: string;
  // null = the picker was never touched → `effectiveAccountIds()` picks the default.
  accountIds: string[] | null;
};

export const EMPTY_FORM: DiscoveryFormState = {
  keywords: '',
  seedChannel: '',
  minSubscribers: '',
  maxSubscribers: '',
  kind: 'channels',
  category: 'any',
  language: 'any',
  comments: 'any',
  access: 'any',
  hideSeen: true,
  limit: '',
  accountIds: null,
};

/** Split a free-form blob on commas/whitespace, drop @-noise, dedupe, cap.
 *
 * Exported so the form gets the kept and the dropped tokens from ONE pass: asking for
 * them separately re-split the whole blob on every keystroke.
 */
export function splitKeywords(raw: string): { keywords: string[]; dropped: string[] } {
  const seen = new Set<string>();
  const keywords: string[] = [];
  // A set, so the same rejected word typed twice is named once.
  const dropped = new Set<string>();
  for (const token of raw.split(/[,\s]+/)) {
    const cleaned = token.trim().replace(/^@+/, '');
    if (cleaned === '') continue;
    // Code points, not UTF-16 units: the backend's length bounds are Python's, so
    // two emoji counted as four here passed the form and 422'd on the server.
    const length = [...cleaned].length;
    if (length < KEYWORD_MIN_LENGTH || length > KEYWORD_MAX_LENGTH) {
      dropped.add(cleaned);
      continue;
    }
    const key = cleaned.toLowerCase();
    // A duplicate is not worth naming — the operator typed the word they meant.
    if (seen.has(key)) continue;
    if (keywords.length === MAX_KEYWORDS) {
      dropped.add(cleaned);
      continue;
    }
    seen.add(key);
    keywords.push(cleaned);
  }
  return { keywords, dropped: [...dropped] };
}

export function parseKeywords(raw: string): string[] {
  return splitKeywords(raw).keywords;
}

/** Append suggested keywords to what the operator typed, leaving their text alone.
 *
 * Re-parsing the whole blob and writing the result back would silently delete the
 * tokens `splitKeywords` drops — and a topic too short to search with ("MMA") is
 * exactly the sort of thing the operator asks the suggester about, so it must survive
 * its own answer. Hence: keep the typed text verbatim and add only the suggestions
 * that got past the dedup, the length bounds and `MAX_KEYWORDS`. The merged list
 * always starts with the typed tokens, in order, so slicing past them leaves the new
 * ones — and when the cap bites it is the operator's own words that keep the slots.
 *
 * A suggestion that is not exactly one token is dropped WHOLE, never taken apart. This
 * field is separator-delimited and the search posts `parseKeywords` of it, so letting a
 * phrase through would enter it as fragments: "бои без правил" would land as "правил"
 * alone, which reads like a keyword the operator has no reason to doubt and then spends
 * a real Telegram read out of the run's small per-run budget. One suggestion fewer is
 * cheaper than one plausible-looking fragment nobody asked for.
 */
export function mergeKeywords(raw: string, suggested: string[]): string {
  // The tokenizer's own separator class, so nothing suggested can ever be split.
  const whole = suggested.filter((phrase) => !/[,\s]/u.test(phrase.trim()));
  const before = parseKeywords(raw).length;
  const added = parseKeywords(`${raw}, ${whole.join(', ')}`).slice(before);
  if (added.length === 0) return raw;
  // A field left mid-word ("crypto, ") would otherwise grow a doubled separator.
  return [raw.trim().replace(/[,\s]+$/u, ''), ...added].filter(Boolean).join(', ');
}

// Digits only: the inputs are `type="text"` (a `number` field reports '' for garbage, so
// "1e3" and "-5" silently became "no bound"), and `Number()` would take both.
function positiveInt(raw: string): number | undefined {
  const trimmed = raw.trim();
  return /^\d+$/.test(trimmed) ? Number(trimmed) : undefined;
}

/** A typed bound that is not a whole number. '' means "no bound" and is fine. */
export function boundInvalid(raw: string): boolean {
  return raw.trim() !== '' && positiveInt(raw) === undefined;
}

/** Turn the form into a wire request, omitting empty filters rather than sending nulls.
 *
 * `accountIds` is the resolved pick (`effectiveAccountIds`), not the form's own field:
 * the form stores null for "untouched" and the default depends on the account list.
 * The filters always travel, defaults included — the server's defaults may drift.
 */
export function buildSearchRequest(
  form: DiscoveryFormState,
  accountIds: string[],
): DiscoverySearchRequest {
  // A pick left over from 'channels' must not 422 the whole request.
  const normal = normalizeForKind(form);
  const request: DiscoverySearchRequest = {
    keywords: parseKeywords(form.keywords),
    kind: form.kind,
    category: form.category,
    language: form.language,
    comments: normal.comments,
    access: normal.access,
    hide_seen: form.hideSeen,
    limit: parseLimit(form.limit) ?? LIMIT_DEFAULT,
    account_ids: accountIds,
  };
  // The placeholder invites a t.me link and the API caps this field at 32 chars, so a
  // pasted URL either 422s or resolves to nothing — strip the prefix instead.
  const seed = stripSeed(form.seedChannel);
  if (seed !== '') request.seed_channel = seed;
  const min = positiveInt(form.minSubscribers);
  const max = positiveInt(form.maxSubscribers);
  if (min !== undefined) request.members_min = min;
  if (max !== undefined) request.members_max = max;
  return request;
}

/** Are the subscriber bounds the wrong way round? The API refuses members_min > members_max. */
export function boundsInverted(form: DiscoveryFormState): boolean {
  const min = positiveInt(form.minSubscribers);
  const max = positiveInt(form.maxSubscribers);
  return min !== undefined && max !== undefined && min > max;
}

/**
 * Can the form be submitted? The API needs something to search on — keywords or a
 * category (its word bundle) — and 1..MAX_SEARCH_ACCOUNTS accounts, so surface that
 * here instead of letting the request 422. A seed channel alone is still not enough.
 */
export function canSubmit(form: DiscoveryFormState, accountIds: string[]): boolean {
  const searchable = parseKeywords(form.keywords).length > 0 || form.category !== 'any';
  return (
    searchable &&
    !boundInvalid(form.minSubscribers) &&
    !boundInvalid(form.maxSubscribers) &&
    !boundsInverted(form) &&
    !seedInvalid(form.seedChannel) &&
    parseLimit(form.limit) !== undefined &&
    accountIds.length > 0 &&
    accountIds.length <= MAX_SEARCH_ACCOUNTS
  );
}

/**
 * Is this row eligible to adopt? Comments must not be known-off, and the channel
 * must be free — the one-active-campaign-per-channel guard would refuse the rest.
 * A group or a subscription-gated channel is not a place the campaign can comment
 * in at all, and the adopt endpoint answers 'not_adoptable' for both. A private row
 * (`id:` — no username) loses its access badge once the in-memory verdict is gone after
 * a restart, so it is refused by its name rather than trusted on a missing flag.
 */
export function isSelectable(candidate: DiscoveryCandidate): boolean {
  if (candidate.in_campaign === true) return false;
  if (candidate.taken_by_other_campaign === true) return false;
  if (candidate.kind === 'group' || candidate.access === 'subscription') return false;
  if (isPrivateRef(candidate.channel)) return false;
  return candidate.qualification !== 'comments_off';
}

/** A private (no-username) row: the backend's PRIVATE_PREFIX ref, not a handle. */
export function isPrivateRef(channel: string): boolean {
  return channel.startsWith('id:');
}

export function selectableChannels(candidates: DiscoveryCandidate[]): string[] {
  return candidates.filter(isSelectable).map((candidate) => candidate.channel);
}

/** Intersect the operator's picks with the LATEST eligible set, capped for the API. */
export function resolveSelection(
  selected: ReadonlySet<string>,
  candidates: DiscoveryCandidate[],
): string[] {
  return selectableChannels(candidates)
    .filter((channel) => selected.has(channel))
    .slice(0, MAX_ADOPT);
}

/** Sort order for the listed rows: adoptable candidates first, then by subscribers
 * descending, unknown counts last. `Array.prototype.sort` is stable in every engine
 * this app ships to, and returning 0 on every remaining tie relies on exactly that —
 * so within one group (adoptable/not, same subscriber count or both unknown) rows
 * keep the order the board sent them in. */
export function compareCandidates(a: DiscoveryCandidate, b: DiscoveryCandidate): number {
  const aSelectable = isSelectable(a);
  const bSelectable = isSelectable(b);
  if (aSelectable !== bSelectable) return aSelectable ? -1 : 1;
  const aSubs = a.subscribers ?? null;
  const bSubs = b.subscribers ?? null;
  if (aSubs === null) return bSubs === null ? 0 : 1;
  if (bSubs === null) return -1;
  return bSubs - aSubs;
}

export function formatSubscribers(count: number | null | undefined, locale: string): string {
  if (count === null || count === undefined) return '—';
  return new Intl.NumberFormat(locale, { notation: 'compact', maximumFractionDigits: 1 }).format(
    count,
  );
}
