// Telephony queries and mutations for a phone's cellular line and inbound SMS inbox.
// The gateway webhook is deliberately absent: only SMS gateways call it, never the SPA.
export {
  getPhoneTelephonyStatusOptions as phoneTelephonyStatusOptions,
  getPhoneTelephonyStatusQueryKey as phoneTelephonyStatusQueryKey,
  listSmsInboxOptions as smsInboxQueryOptions,
  listSmsInboxQueryKey as smsInboxQueryKey,
  manualInputPhoneOtpMutation,
  startPhoneOtpWaitMutation,
} from '@/shared/api/@tanstack/react-query.gen';
