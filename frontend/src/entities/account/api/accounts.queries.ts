// The account entity's data-access surface. Wraps the generated TanStack Query
// options from shared/api (the only data seam, per the FSD ADR) so pages depend
// on the entity, not on the generated client's internals.
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
