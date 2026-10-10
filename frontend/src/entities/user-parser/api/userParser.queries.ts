// User-parser data access, re-exported from the generated client (FSD: data only via
// shared/api). The generated `operation_id` names are the contract.
export type UserParserExportFormat = 'csv' | 'json';

export {
  createUserParserPresetMutation,
  deleteUserParserBaseMutation,
  deleteUserParserPresetMutation,
  getUserParserRunOptions as userParserRunQueryOptions,
  listUserParserAccountsOptions as userParserAccountsQueryOptions,
  listUserParserBasesOptions as userParserBasesQueryOptions,
  listUserParserBaseUsersInfiniteOptions as userParserBaseUsersInfiniteOptions,
  listUserParserPresetsOptions as userParserPresetsQueryOptions,
  listUserParserRunUsersInfiniteOptions as userParserRunUsersInfiniteOptions,
  renameUserParserBaseMutation,
  startUserParserRunMutation,
  stopUserParserRunMutation,
} from '@/shared/api/@tanstack/react-query.gen';

// A plain link, not a fetch: the browser streams the file to disk with the session
// cookie and the server's `Content-Disposition` name, so a 50 000-row base never sits in
// memory as a blob. Same idiom as the account avatar's <img src>.
export function userParserExportUrl(runId: string, format: UserParserExportFormat): string {
  return `/api/v1/user-parser/runs/${encodeURIComponent(runId)}/export?format=${format}`;
}
