import type { AccountRead } from '@/shared/api';

export type AccountStatus = AccountRead['status'];
export type AccountHealth = 'ok' | 'warn' | 'fail';

// Permanent (red) statuses — mirrors the backend's health_for_status so the
// locale-neutral status code maps to a traffic-light health on the frontend.
const PERMANENT: ReadonlySet<AccountStatus> = new Set([
  'unauthorized',
  'session_error',
  'account_error',
]);

export function accountHealth(status: AccountStatus): AccountHealth {
  if (status === 'alive') return 'ok';
  if (PERMANENT.has(status)) return 'fail';
  return 'warn';
}

// The design has four status colours (active/spam/code/banned). Map the
// backend's locale-neutral status codes onto that visual vocabulary so the pill
// and mono-avatar render exactly the design's palette.
export type DesignStatus = 'active' | 'spam' | 'code' | 'banned';

export function accountDesignStatus(status: AccountStatus): DesignStatus {
  if (status === 'alive') return 'active';
  if (status === 'flood_wait') return 'spam';
  return 'banned';
}
