import type { PhoneTelephonyStatus, SmsInboxMessage } from '@/shared/api';
import type { BadgeTone } from '@/shared/ui';

// The generated client inlines these literal unions, so they are named here.
export type TelephonyLineStatus = NonNullable<PhoneTelephonyStatus['status']>;
export type TelephonyPlatform = NonNullable<SmsInboxMessage['platform']>;

export type { PhoneTelephonyStatus, SmsInboxMessage };

export const TELEPHONY_STATUS_TONE: Record<TelephonyLineStatus, BadgeTone> = {
  idle: 'neutral',
  waiting_otp: 'info',
  otp_received: 'warning',
  typing: 'warning',
  completed: 'success',
  failed: 'danger',
  timeout: 'danger',
};

// Statuses that change on their own (a timer runs out, an SMS arrives, typing ends):
// only these are worth polling for. An idle or ended line changes only when the
// operator acts, and that action invalidates the status itself.
const LIVE_STATUSES: ReadonlySet<TelephonyLineStatus> = new Set([
  'waiting_otp',
  'otp_received',
  'typing',
]);

export function isLiveTelephonyStatus(status: TelephonyLineStatus | undefined): boolean {
  return status !== undefined && LIVE_STATUSES.has(status);
}

export function isTelephonyLineStatus(value: unknown): value is TelephonyLineStatus {
  return (
    typeof value === 'string' && Object.prototype.hasOwnProperty.call(TELEPHONY_STATUS_TONE, value)
  );
}

export function formatOtpCountdown(seconds: number): string {
  const clamped = Math.max(0, Math.floor(seconds));
  const mins = Math.floor(clamped / 60);
  const secs = clamped % 60;
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}
