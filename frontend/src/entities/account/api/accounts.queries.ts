// The account entity's data-access surface. Wraps the generated TanStack Query
// options from shared/api (the only data seam, per the FSD ADR) so pages depend
// on the entity, not on the generated client's internals.
export {
  accountStatsOptions as accountStatsQueryOptions,
  getAccountProfileSnapshotOptions as accountProfileSnapshotQueryOptions,
  listAccountsOptions as accountsQueryOptions,
} from '@/shared/api/@tanstack/react-query.gen';
