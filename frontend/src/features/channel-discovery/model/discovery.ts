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
  language: string;
  country: string;
  minSubscribers: string;
  maxSubscribers: string;
  useTelemetr: boolean;
};

export const EMPTY_FORM: DiscoveryFormState = {
  keywords: '',
  seedChannel: '',
  language: '',
  country: '',
  minSubscribers: '',
  maxSubscribers: '',
  useTelemetr: false,
};

/** Split a free-form blob on commas/whitespace, drop @-noise, dedupe, cap. */
export function parseKeywords(raw: string): string[] {
  const seen = new Set<string>();
  const keywords: string[] = [];
  for (const token of raw.split(/[,\s]+/)) {
    const cleaned = token.trim().replace(/^@+/, '');
    if (cleaned.length < KEYWORD_MIN_LENGTH || cleaned.length > KEYWORD_MAX_LENGTH) continue;
    const key = cleaned.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    keywords.push(cleaned);
  }
  return keywords.slice(0, MAX_KEYWORDS);
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
  const seed = form.seedChannel.trim().replace(/^@+/, '');
  if (seed !== '') request.seed_channel = seed;
  if (form.language !== '') request.language = form.language;
  if (form.country !== '') request.country = form.country;
  const min = positiveInt(form.minSubscribers);
  const max = positiveInt(form.maxSubscribers);
  if (min !== undefined) request.members_min = min;
  if (max !== undefined) request.members_max = max;
  return request;
}

/**
 * Can the form be submitted? The API requires at least one keyword even when a seed
 * channel is given, so surface that here instead of letting the request 422.
 */
export function canSubmit(form: DiscoveryFormState): boolean {
  return parseKeywords(form.keywords).length > 0;
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
