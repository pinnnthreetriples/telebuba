// Masstagging data access, re-exported from the generated client (FSD: data only via
// shared/api). The generated `operation_id` names are the contract.
export {
  actOnMasstaggingTargetMutation,
  copyMasstaggingBaseMutation,
  createMasstaggingCampaignMutation,
  deleteMasstaggingCampaignMutation,
  getMasstaggingBoardOptions as masstaggingBoardQueryOptions,
  // Deliberately NOT part of the page's log-stream invalidation set: it backs an
  // explicit-save dialog and refreshes from its own mutation.
  getMasstaggingSettingsOptions as masstaggingSettingsQueryOptions,
  importMasstaggingTargetsMutation,
  listMasstaggingCampaignsOptions as masstaggingCampaignsQueryOptions,
  saveMasstaggingPaceMutation,
  saveMasstaggingSettingsMutation,
  startMasstaggingCampaignMutation,
  stopMasstaggingCampaignMutation,
  uploadMasstaggingMediaMutation,
} from '@/shared/api/@tanstack/react-query.gen';
