import type { AddDeviceRequest } from '@/shared/api';

// One Telegram sign-in per device: an account has a desktop session, a mobile
// session, or both (two separate sign-ins, never one session as two devices).
export type DeviceType = AddDeviceRequest['device_type'];
// What the operator picks when adding an account; `both` adds the other device as
// a second sign-in once the first one exists.
export type DeviceChoice = DeviceType | 'both';

export const DEVICE_TYPES: readonly DeviceType[] = ['desktop', 'mobile'];

export function otherDevice(device: DeviceType): DeviceType {
  return device === 'desktop' ? 'mobile' : 'desktop';
}
