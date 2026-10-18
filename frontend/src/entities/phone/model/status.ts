import type { Phone } from '@/shared/api';
import type { BadgeTone } from '@/shared/ui';

export type PhoneStatus = Phone['status'];

// The farm's five states: asleep is grey, an idle running phone green, the two that
// are busy (warming, an operator's hands) amber and blue, a boot in flight blue.
export const PHONE_STATUS_TONE: Record<PhoneStatus, BadgeTone> = {
  asleep: 'neutral',
  starting: 'info',
  free: 'success',
  warming: 'warning',
  manual: 'info',
};

// What the farm's buttons may do with a phone in each state (the backend refuses the
// rest anyway; this only keeps the buttons honest).
export function phoneActions(status: PhoneStatus) {
  return {
    start: status === 'asleep',
    stop: status === 'free' || status === 'manual',
    control: status === 'free' || status === 'manual',
    watch: status === 'warming',
  };
}

// "SDK 34" as the Android release an operator knows it by; anything else as given.
const ANDROID_RELEASE: Record<number, number> = { 31: 12, 32: 12, 33: 13, 34: 14, 35: 15, 36: 16 };

export function androidVersion(systemVersion: string): string {
  const sdk = /^SDK (\d+)$/.exec(systemVersion.trim())?.[1];
  const release = sdk === undefined ? undefined : ANDROID_RELEASE[Number(sdk)];
  return release === undefined ? systemVersion : `Android ${String(release)}`;
}
