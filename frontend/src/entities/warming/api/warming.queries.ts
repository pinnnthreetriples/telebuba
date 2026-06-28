// Warming data access, re-exported from the generated client (FSD: data only
// via shared/api). The board is one poll tick (idle/warming + channels + settings).
export {
  addWarmingChannelsMutation,
  getWarmingBoardOptions as warmingBoardQueryOptions,
  getWarmingSettingsOptions as warmingSettingsQueryOptions,
  listWarmingChannelsOptions as warmingChannelsQueryOptions,
  removeWarmingChannelMutation,
  startWarmingMutation,
  stopWarmingMutation,
  updateWarmingSettingsMutation,
} from '@/shared/api/@tanstack/react-query.gen';
