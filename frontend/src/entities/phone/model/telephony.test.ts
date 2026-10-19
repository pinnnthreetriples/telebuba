import { describe, expect, it } from 'vitest';

import { formatOtpCountdown, isLiveTelephonyStatus, isTelephonyLineStatus } from './telephony';

describe('isLiveTelephonyStatus', () => {
  it('is live only while the line can change on its own', () => {
    expect(isLiveTelephonyStatus('waiting_otp')).toBe(true);
    expect(isLiveTelephonyStatus('otp_received')).toBe(true);
    expect(isLiveTelephonyStatus('typing')).toBe(true);
    for (const ended of ['idle', 'completed', 'failed', 'timeout'] as const) {
      expect(isLiveTelephonyStatus(ended)).toBe(false);
    }
    expect(isLiveTelephonyStatus(undefined)).toBe(false);
  });
});

describe('isTelephonyLineStatus', () => {
  it('accepts the known statuses only', () => {
    expect(isTelephonyLineStatus('failed')).toBe(true);
    expect(isTelephonyLineStatus('constructor')).toBe(false);
    expect(isTelephonyLineStatus(3)).toBe(false);
  });
});

describe('formatOtpCountdown', () => {
  it('formats minutes and seconds and never goes negative', () => {
    expect(formatOtpCountdown(179)).toBe('02:59');
    expect(formatOtpCountdown(5.9)).toBe('00:05');
    expect(formatOtpCountdown(-3)).toBe('00:00');
  });
});
