// Telephony queries and mutations for a phone's cellular line and inbound SMS inbox.
export {
  getPhoneTelephonyStatusOptions as phoneTelephonyStatusOptions,
  getPhoneTelephonyStatusQueryKey as phoneTelephonyStatusQueryKey,
  listSmsInboxOptions as smsInboxQueryOptions,
  listSmsInboxQueryKey as smsInboxQueryKey,
  manualInputPhoneOtpMutation,
  receiveInboundSmsWebhookMutation,
} from '@/shared/api/@tanstack/react-query.gen';
