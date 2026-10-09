// Chat-broadcast data access, re-exported from the generated client (FSD: data only via
// shared/api). The generated `operation_id` names are the contract.
export {
  actOnChatBroadcastTargetMutation,
  createChatBroadcastCampaignMutation,
  createChatCollectionMutation,
  deleteChatBroadcastCampaignMutation,
  deleteChatCollectionMutation,
  getChatBroadcastBoardOptions as chatBroadcastBoardQueryOptions,
  // Deliberately NOT part of the page's log-stream invalidation set: it backs an
  // explicit-save dialog and refreshes from its own mutation.
  getChatBroadcastSettingsOptions as chatBroadcastSettingsQueryOptions,
  listChatBroadcastCampaignsOptions as chatBroadcastCampaignsQueryOptions,
  listChatBroadcastOwnChatsOptions as chatBroadcastOwnChatsQueryOptions,
  // Saved reusable chat lists («Категории»). A category is copied into a campaign.
  listChatCollectionsOptions as chatCollectionsQueryOptions,
  resolveChatBroadcastTargetsMutation,
  saveChatBroadcastPaceMutation,
  saveChatBroadcastSettingsMutation,
  saveChatCollectionMutation,
  startChatBroadcastCampaignMutation,
  stopChatBroadcastCampaignMutation,
  uploadChatBroadcastPhotoMutation,
} from '@/shared/api/@tanstack/react-query.gen';
