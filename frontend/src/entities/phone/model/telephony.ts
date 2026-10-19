import type {
  OtpSession,
  PhoneTelephonyStatus,
  SmsInboxMessage,
  TelephonyLineStatus,
  TelephonyPlatform,
} from '@/shared/api';
import type { BadgeTone } from '@/shared/ui';

export type {
  OtpSession,
  PhoneTelephonyStatus,
  SmsInboxMessage,
  TelephonyLineStatus,
  TelephonyPlatform,
};

export const TELEPHONY_STATUS_TONE: Record<TelephonyLineStatus, BadgeTone> = {
  idle: 'neutral',
  waiting_otp: 'info',
  otp_received: 'warning',
  typing: 'warning',
  completed: 'success',
  timeout: 'danger',
};

export function isTelephonyLineStatus(value: unknown): value is TelephonyLineStatus {
  return (
    typeof value === 'string' &&
    Object.prototype.hasOwnProperty.call(TELEPHONY_STATUS_TONE, value)
  );
}

export function formatOtpCountdown(seconds: number): string {
  const clamped = Math.max(0, Math.floor(seconds));
  const mins = Math.floor(clamped / 60);
  const secs = clamped % 60;
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}
