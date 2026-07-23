// The account entity's data-access surface. Wraps the generated TanStack Query
// options from shared/api (the only data seam, per the FSD ADR) so pages depend
// on the entity, not on the generated client's internals.
import { queryOptions } from '@tanstack/react-query';

import { listAccounts, type AccountRead } from '@/shared/api';

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

export {
  accountStatsOptions as accountStatsQueryOptions,
  accountStatsQueryKey,
  checkAccountChannelUsernameOptions as accountChannelUsernameCheckQueryOptions,
  checkAccountChannelUsernameQueryKey as accountChannelUsernameCheckQueryKey,
  getAccountChannelOptions as accountChannelQueryOptions,
  getAccountChannelQueryKey as accountChannelQueryKey,
  getAccountProfileSnapshotOptions as accountProfileSnapshotQueryOptions,
  getAccountProfileSnapshotQueryKey as accountProfileSnapshotQueryKey,
  listAccountChannelPostsOptions as accountChannelPostsQueryOptions,
  listAccountChannelPostsQueryKey as accountChannelPostsQueryKey,
  listAccountChannelsOptions as accountChannelsQueryOptions,
  listAccountChannelsQueryKey as accountChannelsQueryKey,
  listAccountsOptions as accountsQueryOptions,
  listAccountsQueryKey as accountsQueryKey,
} from '@/shared/api/@tanstack/react-query.gen';
