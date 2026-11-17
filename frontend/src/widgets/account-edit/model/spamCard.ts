import type { AccountRead, SpamCheckResult } from '@/shared/api';
import type { BadgeTone } from '@/shared/ui';

// What the account card says about spam. The backend derives every value (the verdict, its
// source, the instants, whether a click is out of turn); the card only words them.

export type SpamVerdict = 'clean' | 'limited' | 'unknown' | 'frozen';

// The folded verdict when the backend sent one, else the stored reading (an older payload).
export function spamVerdictOf(account: AccountRead): SpamVerdict {
  return account.spam_verdict ?? account.spam_status ?? 'unknown';
}

export const VERDICT_TONE: Record<SpamVerdict, BadgeTone> = {
  clean: 'success',
  limited: 'danger',
  frozen: 'danger',
  unknown: 'neutral',
};

// The traffic-light dot of a row, per verdict.
export const VERDICT_DOT: Record<SpamVerdict, string> = {
  clean: 'bg-success',
  limited: 'bg-danger',
  frozen: 'bg-danger',
  unknown: 'bg-line-strong',
};

// An ISO instant as epoch milliseconds, `null` when absent or not a date.
export function instantMs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : ms;
}

// The next check is worth naming only while it is still ahead.
export function upcomingMs(iso: string | null | undefined, now: number): number | null {
  const ms = instantMs(iso);
  return ms !== null && ms > now ? ms : null;
}

export type AppealView =
  | { kind: 'hidden' }
  // Limited and nothing filed: the operator may open the window and record the filing.
  | { kind: 'available' }
  // Filed and not yet answered by a clean: who, when, and when it is re-checked.
  | { kind: 'filed'; filedAt: string | null; filedBy: string | null; recheckAt: string | null }
  // The newest appeal ended (a clean answer): history only.
  | { kind: 'closed'; filedAt: string | null; filedBy: string | null };

type AppealStatus = NonNullable<AccountRead['spam_appeal_status']>;

// What the appeal block shows. `status` / `filed*` come from the card's own read of the
// appeal (fresher after a press); the account row's summary is the fallback.
export function appealView(
  account: AccountRead,
  state:
    | {
        status?: AppealStatus;
        filed_at?: string | null;
        filed_by?: string | null;
        recheck_at?: string | null;
      }
    | undefined,
): AppealView {
  const status = state?.status ?? account.spam_appeal_status ?? 'none';
  const filedAt = state?.filed_at ?? account.spam_appeal_filed_at ?? null;
  const filedBy = state?.filed_by ?? account.spam_appeal_filed_by ?? null;
  if (status === 'open') {
    return { kind: 'filed', filedAt, filedBy, recheckAt: state?.recheck_at ?? null };
  }
  if (spamVerdictOf(account) === 'limited') return { kind: 'available' };
  if (status === 'closed') return { kind: 'closed', filedAt, filedBy };
  return { kind: 'hidden' };
}

// A check the budget held back or answered from the last reading, instead of asking @SpamBot:
// the card shows why and when it can be asked again, never an empty answer.
export function isHeld(result: SpamCheckResult): boolean {
  return (
    result.served_from === 'deferred' ||
    result.served_from === 'refused' ||
    result.served_from === 'coalesced'
  );
}
