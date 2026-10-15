import type { OutreachProtection, SkippedAccount } from '@/shared/api';

export type FullProtection = Required<OutreachProtection>;

// The server's defaults, for the moment before the saved limits load.
export const DEFAULT_PROTECTION: FullProtection = {
  young_half_caps: true,
  young_account_days: 30,
  rest_after_limit: true,
  rest_hours: 12,
  stop_run_on_limits: true,
  stop_run_after_accounts: 2,
  skip_injured: true,
  typing: true,
  typing_min_seconds: 2,
  typing_max_seconds: 5,
};

export function fullProtection(protection: OutreachProtection | undefined): FullProtection {
  return { ...DEFAULT_PROTECTION, ...protection };
}

// A young account's daily cap, as the server computes it: half, never below one.
export function youngCap(cap: number): number {
  return Math.max(Math.floor(cap / 2), 1);
}

// The accounts a run with these switches would leave out: a rest counts only while
// rests are on, a spam block or a fresh flood only while injured accounts are skipped.
export function leftOut(
  skipped: SkippedAccount[] | undefined,
  protection: FullProtection,
): Map<string, SkippedAccount> {
  return new Map(
    (skipped ?? [])
      .filter((row) =>
        row.reason === 'account_resting' ? protection.rest_after_limit : protection.skip_injured,
      )
      .map((row) => [row.account_id, row]),
  );
}
