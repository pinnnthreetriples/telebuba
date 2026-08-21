// The account entity's data-access surface. Wraps the generated TanStack Query
// options from shared/api (the only data seam, per the FSD ADR) so pages depend
// on the entity, not on the generated client's internals.
import { queryOptions, type QueryClient } from '@tanstack/react-query';

import {
  getAccountProfileSnapshot,
  listAccounts,
  type AccountProfileView,
  type AccountRead,
} from '@/shared/api';
import {
  listAccountsQueryKey,
  listProxiesQueryKey,
  accountStatsQueryKey as statsQueryKey,
} from '@/shared/api/@tanstack/react-query.gen';

// The backend caps a page at 200 (api/v1/accounts.py), so pull at that size.
const ALL_ACCOUNTS_PAGE_SIZE = 200;

// Every account across all pages, for views that need the full id→label fleet
// (log filters, neurocomment candidates) rather than one server page. Follows
// next_cursor until exhausted.
export function allAccountsQueryOptions() {
  return queryOptions({
    queryKey: ['allAccounts'] as const,
    queryFn: async ({ signal }) => {
      const items: AccountRead[] = [];
      let cursor: string | null | undefined;
      const seen = new Set<string>();
      do {
        // Guard against a buggy/changed backend cursor contract (repeated or
        // never-null cursor) that would otherwise loop forever and hammer the API.
        if (cursor != null) {
          if (seen.has(cursor)) break;
          seen.add(cursor);
        }
        const { data } = await listAccounts({
          query: { cursor: cursor ?? undefined, limit: ALL_ACCOUNTS_PAGE_SIZE },
          throwOnError: true,
          signal,
        });
        items.push(...data.items);
        cursor = data.next_cursor;
      } while (cursor);
      return { items };
    },
  });
}

// What an account action actually invalidates: the accounts table, the fleet
// stat tiles, and proxy usage (an account holds a pool slot) — NOT the whole
// cache. A bare `invalidateQueries()` made an @SpamBot check refetch the warming
// board, the neurocomment campaigns, the logs and every open profile snapshot,
// and it refetched the accounts list the edit view derives its account from, so
// a row dropping off the current cursor page unmounted the view mid-input. One
// definition, so the page and every account-edit card scope it identically.
// The proxy key is read from the generated client rather than through
// `entities/proxy` — an entity must not cross-import a sibling slice.
export function invalidateAccountViews(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ queryKey: listAccountsQueryKey() });
  void queryClient.invalidateQueries({ queryKey: statsQueryKey() });
  void queryClient.invalidateQueries({ queryKey: listProxiesQueryKey() });
}

// One-shot forced live pull for the profile modal (refresh=true bypasses the
// server's 30s read cache). Calls the SDK directly — not fetchQuery — so no
// parallel refresh:true entry lands in the query cache; the caller writes the
// result into the plain snapshot key itself.
export async function fetchLiveProfileSnapshot(accountId: string): Promise<AccountProfileView> {
  const { data } = await getAccountProfileSnapshot({
    path: { account_id: accountId },
    query: { refresh: true },
    throwOnError: true,
  });
  return data;
}

export {
  accountStatsOptions as accountStatsQueryOptions,
  accountStatsQueryKey,
  checkAccountChannelUsernameOptions as accountChannelUsernameCheckQueryOptions,
  checkAccountChannelUsernameQueryKey as accountChannelUsernameCheckQueryKey,
  getAccountChannelOptions as accountChannelQueryOptions,
  getAccountChannelQueryKey as accountChannelQueryKey,
  getAccountPrivacyOptions as accountPrivacyQueryOptions,
  getAccountPrivacyQueryKey as accountPrivacyQueryKey,
  getAccountProfileSnapshotOptions as accountProfileSnapshotQueryOptions,
  getAccountProfileSnapshotQueryKey as accountProfileSnapshotQueryKey,
  getAccountTwofaOptions as accountTwofaQueryOptions,
  getAccountTwofaQueryKey as accountTwofaQueryKey,
  listAccountChannelPostsOptions as accountChannelPostsQueryOptions,
  listAccountChannelPostsQueryKey as accountChannelPostsQueryKey,
  listAccountChannelsOptions as accountChannelsQueryOptions,
  listAccountChannelsQueryKey as accountChannelsQueryKey,
  listAccountsOptions as accountsQueryOptions,
  listAccountsQueryKey as accountsQueryKey,
} from '@/shared/api/@tanstack/react-query.gen';
