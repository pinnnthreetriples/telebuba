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

// The maker the mockup's chin shows: the model's first word ("Samsung Galaxy S23").
export function phoneBrand(model: string): string {
  return model.trim().split(/\s+/)[0] ?? model;
}
