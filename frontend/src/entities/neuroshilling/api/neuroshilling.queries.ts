// Neuroshilling data access, re-exported from the generated client (FSD: data
// only via shared/api). The generated `operation_id` names are the contract —
// renaming one on the backend renames the function here, so the aliases below
// stay thin and mechanical.
export {
  createNeuroshillingCampaignMutation,
  deleteNeuroshillingCampaignMutation,
  getNeuroshillingBoardOptions as neuroshillingBoardQueryOptions,
  listNeuroshillingCampaignsOptions as neuroshillingCampaignsQueryOptions,
  updateNeuroshillingCampaignMutation,
} from '@/shared/api/@tanstack/react-query.gen';
