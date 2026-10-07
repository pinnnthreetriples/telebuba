// Chat-broadcast data access, re-exported from the generated client (FSD: data only via
// shared/api). The generated `operation_id` names are the contract.
export {
  actOnChatBroadcastTargetMutation,
  createChatBroadcastCampaignMutation,
  deleteChatBroadcastCampaignMutation,
  getChatBroadcastBoardOptions as chatBroadcastBoardQueryOptions,
  // Deliberately NOT part of the page's log-stream invalidation set: it backs an
  // explicit-save dialog and refreshes from its own mutation.
  getChatBroadcastSettingsOptions as chatBroadcastSettingsQueryOptions,
  listChatBroadcastCampaignsOptions as chatBroadcastCampaignsQueryOptions,
  listChatBroadcastOwnChatsOptions as chatBroadcastOwnChatsQueryOptions,
  resolveChatBroadcastTargetsMutation,
  saveChatBroadcastPaceMutation,
  saveChatBroadcastSettingsMutation,
  startChatBroadcastCampaignMutation,
  stopChatBroadcastCampaignMutation,
  uploadChatBroadcastPhotoMutation,
} from '@/shared/api/@tanstack/react-query.gen';
