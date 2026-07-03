// Warming data access, re-exported from the generated client (FSD: data only
// via shared/api). The board is one poll tick (idle/warming + channels + settings).
export {
  addWarmingChannelsMutation,
  getWarmingBoardOptions as warmingBoardQueryOptions,
  getWarmingSettingsOptions as warmingSettingsQueryOptions,
  listWarmedAccountsOptions as warmedAccountsQueryOptions,
  listWarmingChannelsOptions as warmingChannelsQueryOptions,
  listWarmingDialoguesOptions as warmingDialoguesQueryOptions,
  promoteToNeurocommentMutation,
  removeWarmingChannelMutation,
  startWarmingMutation,
  stopWarmingMutation,
  unpromoteFromNeurocommentMutation,
  updateWarmingSettingsMutation,
} from '@/shared/api/@tanstack/react-query.gen';
