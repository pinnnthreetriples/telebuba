// Neuroshilling data access, re-exported from the generated client (FSD: data
// only via shared/api). The generated `operation_id` names are the contract —
// renaming one on the backend renames the function here, so the aliases below
// stay thin and mechanical.
export {
  approveNeuroshillingScenarioMutation,
  createNeuroshillingCampaignMutation,
  deleteNeuroshillingCampaignMutation,
  generateNeuroshillingScenarioMutation,
  getNeuroshillingBoardOptions as neuroshillingBoardQueryOptions,
  // Deliberately NOT part of the page's log-stream invalidation set — see the
  // comment on `NEUROSHILLING_QUERY_IDS`. It refreshes from its own mutations.
  getNeuroshillingScenarioOptions as neuroshillingScenarioQueryOptions,
  listNeuroshillingCampaignsOptions as neuroshillingCampaignsQueryOptions,
  setNeuroshillingScenarioMutation,
  startNeuroshillingCampaignMutation,
  stopNeuroshillingCampaignMutation,
  updateNeuroshillingCampaignMutation,
} from '@/shared/api/@tanstack/react-query.gen';
