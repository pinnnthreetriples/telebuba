// The search filters and the account picker, as pure data — no React, no server calls.
// Every tuple is `satisfies` the generated union, so a backend rename of a code fails
// typecheck here instead of shipping a <select> whose option the server 422s.
import type { DiscoveryAccountOption, DiscoverySearchRequest } from '@/shared/api';

type Req = DiscoverySearchRequest;

export type DiscoveryKind = NonNullable<Req['kind']>;
export type DiscoveryCategory = NonNullable<Req['category']>;
export type DiscoveryLanguage = NonNullable<Req['language']>;
export type DiscoveryComments = NonNullable<Req['comments']>;
export type DiscoveryAccess = NonNullable<Req['access']>;

export const KINDS = ['all', 'channels', 'groups'] as const satisfies readonly DiscoveryKind[];

// 'any' first, then the codes in the generated order.
export const CATEGORIES = [
  'any',
  'it_programming',
  'beauty_health',
  'crypto',
  'trading',
  'news',
  'business',
  'marketing',
  'education',
  'entertainment',
  'games',
  'sport',
  'travel',
  'food',
  'cars',
  'real_estate',
  'finance',
  'psychology',
  'humor',
  'music',
  'movies',
  'fashion',
  'politics',
  'science',
  'parenting',
  'jobs',
] as const satisfies readonly DiscoveryCategory[];

export const LANGUAGES = [
  'any',
  'ru',
  'en',
  'uk',
  'other',
] as const satisfies readonly DiscoveryLanguage[];

export const COMMENTS = ['any', 'on', 'off'] as const satisfies readonly DiscoveryComments[];

export const ACCESS = [
  'any',
  'open',
  'join_request',
  'subscription',
] as const satisfies readonly DiscoveryAccess[];

// Mirrors the API's bounds on `limit`; an empty field means "the server default".
export const LIMIT_MIN = 1;
export const LIMIT_MAX = 500;
export const LIMIT_DEFAULT = 200;

/** '' → the default; an integer within bounds → itself; anything else → undefined.
 *
 * Never clamps: a typed "600" silently becoming 500 is a request the operator did not
 * make, so the form refuses it instead (`limitInvalid`).
 */
export function parseLimit(raw: string): number | undefined {
  const trimmed = raw.trim();
  if (trimmed === '') return LIMIT_DEFAULT;
  if (!/^\d+$/.test(trimmed)) return undefined;
  const parsed = Number(trimmed);
  if (parsed < LIMIT_MIN || parsed > LIMIT_MAX) return undefined;
  return parsed;
}

export function limitInvalid(raw: string): boolean {
  return parseLimit(raw) === undefined;
}

/** The accounts a search may use right now: nothing warming, listening or cooling. */
export function eligibleAccountIds(accounts: readonly DiscoveryAccountOption[]): string[] {
  return accounts
    .filter((account) => account.busy_reason == null)
    .map((account) => account.account_id);
}

/** Premium accounts hit FLOOD_PREMIUM_WAIT less often, so they are the default pick
 * whenever there is at least one; otherwise every eligible account is. */
export function defaultAccountIds(accounts: readonly DiscoveryAccountOption[]): string[] {
  const eligible = accounts.filter((account) => account.busy_reason == null);
  const premium = eligible.filter((account) => account.premium === true);
  return (premium.length > 0 ? premium : eligible).map((account) => account.account_id);
}

/** What the request actually carries: an untouched picker (null) means the default,
 * a pick is re-intersected with the LATEST eligible set, in the list's own order. */
export function effectiveAccountIds(
  picked: readonly string[] | null,
  accounts: readonly DiscoveryAccountOption[],
): string[] {
  if (picked === null) return defaultAccountIds(accounts);
  const chosen = new Set(picked);
  return eligibleAccountIds(accounts).filter((id) => chosen.has(id));
}

/** Groups have no comments verdict and never arrive by subscription — the server 422s
 * both, so the form disables them. */
export function groupsDisable(kind: DiscoveryKind): { comments: boolean; subscription: boolean } {
  const groups = kind === 'groups';
  return { comments: groups, subscription: groups };
}
