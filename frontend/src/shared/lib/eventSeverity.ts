import type { LogEntry } from '@/shared/api';

// Display severity for an activity-log line: drives its colour and the "errors" stat.
//
// The backend log LEVEL (INFO/WARNING/ERROR → status success/warning/error) is only a
// coarse signal — many neurocomment outcomes are logged at INFO because they are not
// system faults (a skipped post, a busy account), yet they read as failures in the feed.
// So we classify from the stable event code, same idiom as `eventLabel`'s suffix match:
//   red   = we attempted the work and it failed to produce / deliver output
//   amber = we deliberately held off, paused, or hit a limit
//   green = it worked (or a neutral lifecycle event)
// An unmatched code falls back to the level-derived status, so ERROR rows stay red.
type LogSeverity = 'success' | 'warning' | 'error';

const FAILURE = /(_failed|_exhausted|_crashed|_dropped|_overloaded|_deleted|_banned)$/;
const SOFT =
  /(_skipped|_gated|_cooled|_cooldown|_backoff|_reclaimed|no_account_available|no_campaign|retry_later)$/;

export function logSeverity(line: Pick<LogEntry, 'event' | 'status'>): LogSeverity {
  if (FAILURE.test(line.event)) return 'error';
  if (SOFT.test(line.event)) return 'warning';
  return line.status;
}
