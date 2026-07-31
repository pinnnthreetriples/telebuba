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

// Straight off the generated request type, so the option lists the form renders cannot
// drift from the codes the API accepts — that drift is what sent country=TR to a
// catalogue whose dictionary calls it "turkey", and it answered with an empty page.
export type DiscoveryLanguage = NonNullable<DiscoverySearchRequest['language']>;
export type DiscoveryCountry = NonNullable<DiscoverySearchRequest['country']>;

export type DiscoveryFormState = {
  keywords: string;
  seedChannel: string;
  // '' is the "any" option, which omits the filter entirely.
  language: DiscoveryLanguage | '';
  country: DiscoveryCountry | '';
  minSubscribers: string;
  maxSubscribers: string;
  useTelemetr: boolean;
  catalogueOnly: boolean;
};

export const EMPTY_FORM: DiscoveryFormState = {
  keywords: '',
  seedChannel: '',
  language: '',
  country: '',
  minSubscribers: '',
  maxSubscribers: '',
  useTelemetr: false,
  catalogueOnly: false,
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
    use_telemetr: form.useTelemetr,
  };
  // The placeholder invites a t.me link and the API caps this field at 32 chars, so a
  // pasted URL either 422s or resolves to nothing — strip the prefix instead.
  const seed = form.seedChannel.trim().replace(/^(?:https?:\/\/)?(?:t\.me\/)?@*/i, '');
  if (seed !== '') request.seed_channel = seed;
  // Only the Telemetr.io catalogue filters by locale; Telegram's own search and its
  // similar-channels feed have none, so without that source these reach nothing.
  if (form.useTelemetr) {
    if (form.language !== '') request.language = form.language;
    if (form.country !== '') request.country = form.country;
    // Only meaningful alongside the catalogue, and the API refuses it without.
    if (form.catalogueOnly) request.catalogue_only = true;
  }
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
