// Pure discovery logic — no React, no server calls. Lives in a .ts module because
// react-refresh/only-export-components forbids non-component exports from .tsx.
import type { DiscoveryCandidate, DiscoverySearchRequest } from '@/shared/api';

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
};

export const EMPTY_FORM: DiscoveryFormState = {
  keywords: '',
  seedChannel: '',
  minSubscribers: '',
  maxSubscribers: '',
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

function positiveInt(raw: string): number | undefined {
  const trimmed = raw.trim();
  if (trimmed === '') return undefined;
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || parsed < 0) return undefined;
  return Math.floor(parsed);
}

/** Turn the form into a wire request, omitting empty filters rather than sending nulls. */
export function buildSearchRequest(form: DiscoveryFormState): DiscoverySearchRequest {
  const request: DiscoverySearchRequest = {
    keywords: parseKeywords(form.keywords),
  };
  // The placeholder invites a t.me link and the API caps this field at 32 chars, so a
  // pasted URL either 422s or resolves to nothing — strip the prefix instead.
  const seed = form.seedChannel.trim().replace(/^(?:https?:\/\/)?(?:t\.me\/)?@*/i, '');
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
 * Can the form be submitted? The API requires at least one keyword even when a seed
 * channel is given, so surface that here instead of letting the request 422.
 */
export function canSubmit(form: DiscoveryFormState): boolean {
  return parseKeywords(form.keywords).length > 0 && !boundsInverted(form);
}

/**
 * Is this row eligible to adopt? Comments must not be known-off, and the channel
 * must be free — the one-active-campaign-per-channel guard would refuse the rest.
 */
export function isSelectable(candidate: DiscoveryCandidate): boolean {
  if (candidate.in_campaign === true) return false;
  if (candidate.taken_by_other_campaign === true) return false;
  return candidate.qualification !== 'comments_off';
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

export function formatSubscribers(count: number | null | undefined, locale: string): string {
  if (count === null || count === undefined) return '—';
  return new Intl.NumberFormat(locale, { notation: 'compact', maximumFractionDigits: 1 }).format(
    count,
  );
}
