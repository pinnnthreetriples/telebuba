import type { AccountProtection } from '@/shared/api';

export type ProtectionTone = 'success' | 'warning' | 'danger';

// All of it in place is ok; 5/7 and 6/7 warn; 4/7 and below are red.
export const WARN_FROM = 5;

export function protectionTone({ score, total }: AccountProtection): ProtectionTone {
  if (score >= total) return 'success';
  return score >= WARN_FROM ? 'warning' : 'danger';
}

export function isFullyProtected(protection: AccountProtection | null | undefined): boolean {
  return protection != null && protection.score >= protection.total;
}
