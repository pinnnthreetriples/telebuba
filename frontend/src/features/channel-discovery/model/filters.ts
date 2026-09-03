// The search filters and the account picker, as pure data — no React, no server calls.
// Every tuple is `satisfies` the generated union, so a backend rename of a code fails
// typecheck here instead of shipping a <select> whose option the server 422s.
import type { DiscoveryAccountOption, DiscoverySearchRequest } from '@/shared/api';

import type { DiscoveryFormState } from './discovery';

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

// Mirrors MAX_SEARCH_ACCOUNTS in schemas/neurocomment_discovery_request.py (`maxItems` on
// account_ids in openapi.json, which the generated TS type does not carry).
export const MAX_SEARCH_ACCOUNTS = 10;

/** '' → the default; an integer within bounds → itself; anything else → undefined.
 *
 * Never clamps: a typed "600" silently becoming 500 is a request the operator did not
 * make, so the form refuses it instead (`parseLimit(raw) === undefined`).
 */
export function parseLimit(raw: string): number | undefined {
  const trimmed = raw.trim();
  if (trimmed === '') return LIMIT_DEFAULT;
  if (!/^\d+$/.test(trimmed)) return undefined;
  const parsed = Number(trimmed);
  if (parsed < LIMIT_MIN || parsed > LIMIT_MAX) return undefined;
  return parsed;
}

/** The accounts a search may use right now: nothing warming, listening or cooling. */
export function eligibleAccountIds(accounts: readonly DiscoveryAccountOption[]): string[] {
  return accounts
    .filter((account) => account.busy_reason == null)
    .map((account) => account.account_id);
}

/** What the request actually carries: a pick is re-intersected with the LATEST eligible
 * set, in the list's own order; an untouched picker (null) means the default — up to
 * MAX_SEARCH_ACCOUNTS eligible accounts, premium first (they hit FLOOD_PREMIUM_WAIT
 * less often), then the rest in list order. */
export function effectiveAccountIds(
  picked: readonly string[] | null,
  accounts: readonly DiscoveryAccountOption[],
): string[] {
  const eligible = accounts.filter((account) => account.busy_reason == null);
  if (picked === null) {
    const premium = eligible.filter((account) => account.premium === true);
    const rest = eligible.filter((account) => account.premium !== true);
    return [...premium, ...rest].slice(0, MAX_SEARCH_ACCOUNTS).map((account) => account.account_id);
  }
  const chosen = new Set(picked);
  return eligible.map((account) => account.account_id).filter((id) => chosen.has(id));
}

/** Mirror of the server rule: groups have no comments verdict and never arrive by
 * subscription, and the server 422s both. Applied to the STATE on a kind change (so the
 * UI never shows a disabled option as the selected one) and again when the request is
 * built, so a stale pick cannot slip through either way. */
export function normalizeForKind(form: DiscoveryFormState): DiscoveryFormState {
  if (form.kind !== 'groups') return form;
  return {
    ...form,
    comments: 'any',
    access: form.access === 'subscription' ? 'any' : form.access,
  };
}
