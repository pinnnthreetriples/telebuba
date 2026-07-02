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
// and mono-avatar render exactly the design's palette. The four buckets mirror
// the backend's AccountStats roll-up so the tiles and the per-row badge agree:
//   active → alive · spam(idle) → flood_wait · code → unauthorized/new ·
//   banned(problem) → every other non-alive status.
export type DesignStatus = 'active' | 'spam' | 'code' | 'banned';

// Statuses that need a re-auth login code (the design's blue "code" bucket).
const NEEDS_CODE: ReadonlySet<AccountStatus> = new Set(['unauthorized', 'new']);

export function accountDesignStatus(status: AccountStatus): DesignStatus {
  if (status === 'alive') return 'active';
  if (status === 'flood_wait') return 'spam';
  if (NEEDS_CODE.has(status)) return 'code';
  return 'banned';
}
