// The Accounts page's list filters: which folder tab is open and what the filter card
// narrows it to. All of it is applied server-side — the list paginates with a cursor,
// so filtering one page in the browser would hide matches on the others.
import type { ListAccountsData } from '@/shared/api';

// 'all' and 'unfiled' are the two virtual views; anything else is a folder id.
export type FolderView = string;
export const ALL_VIEW = 'all';
export const UNFILED_VIEW = 'unfiled';

export const STATUS_BUCKETS = ['active', 'idle', 'needs_code', 'problem'] as const;
export type StatusBucket = (typeof STATUS_BUCKETS)[number];

export const TRUST_STEPS = [50, 70, 90] as const;

export interface AccountFilters {
  // A calling code (7, 49, …).
  phone: number | null;
  // A 2-letter country code, or 'none' for accounts without a proxy.
  proxy: string | null;
  status: StatusBucket | null;
  minTrust: number | null;
}

export const NO_FILTERS: AccountFilters = {
  phone: null,
  proxy: null,
  status: null,
  minTrust: null,
};

export type FilterKey = keyof AccountFilters;
export const FILTER_KEYS: readonly FilterKey[] = ['phone', 'proxy', 'status', 'minTrust'];

export function activeFilterKeys(filters: AccountFilters): FilterKey[] {
  return FILTER_KEYS.filter((key) => filters[key] !== null);
}

type ListQuery = NonNullable<ListAccountsData['query']>;

// The list endpoint's query for a view + filters. Unset filters stay out of the query
// rather than going over as empty strings.
export function listQuery(view: FolderView, filters: AccountFilters): ListQuery {
  const query: ListQuery = { status: filters.status ?? 'all' };
  if (view !== ALL_VIEW) query.folder = view;
  if (filters.phone !== null) query.phone_code = filters.phone;
  if (filters.proxy !== null) query.proxy_country = filters.proxy;
  if (filters.minTrust !== null) query.min_trust = filters.minTrust;
  return query;
}

// Status-tile colours, so a status pill reads the same as the tile it filters by.
export const STATUS_DOT: Record<StatusBucket, string> = {
  active: 'bg-success',
  idle: 'bg-warning',
  needs_code: 'bg-action-primary',
  problem: 'bg-danger',
};

// Same labels as the stat tiles.
export const STATUS_LABEL_KEY: Record<StatusBucket, string> = {
  active: 'accounts.stats.active',
  idle: 'accounts.stats.idle',
  needs_code: 'accounts.stats.code',
  problem: 'accounts.stats.problem',
};

// A country's name in the UI language; the proxy geo's own name, then the code, when the
// runtime has no name for it.
export function countryName(code: string, language: string, fallback?: string | null): string {
  try {
    const name = new Intl.DisplayNames([language], { type: 'region' }).of(code.toUpperCase());
    if (name && name !== code.toUpperCase()) return name;
  } catch {
    // An unknown or malformed code throws; fall through to the fallback.
  }
  return fallback ?? code.toUpperCase();
}
