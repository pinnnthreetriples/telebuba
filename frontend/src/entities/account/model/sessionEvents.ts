import { type QueryClient, useQueryClient } from '@tanstack/react-query';

import type { LogEntry } from '@/shared/api';
import { useLogEventStream } from '@/shared/lib';

import {
  accountRekeyQueryKey,
  accountSessionAuditQueryKey,
  invalidateAccountViews,
} from '../api/accounts.queries';

// The events that change what the accounts list and the Sessions card show: an audit that
// found or read the sessions (the background watch runs them without the operator), a close,
// a re-key and the old key's end. Only these refresh anything: the stream carries every
// section's events, hundreds a day.
const SESSION_EVENTS: ReadonlySet<string> = new Set([
  'session_audit_completed',
  'session_audit_foreign_found',
  'session_close_completed',
  'session_rekeyed',
  'session_old_key_closed',
  'session_old_key_pending',
  'session_old_key_resolved',
  'session_old_key_problem',
]);

export function isSessionEvent(entry: Pick<LogEntry, 'event'>): boolean {
  return SESSION_EVENTS.has(entry.event);
}

/** Re-read the list (its foreign-sessions badge) and the card of the account that produced it. */
export function refreshForSessionEvent(
  queryClient: QueryClient,
  entry: Pick<LogEntry, 'event' | 'account_id'>,
): void {
  if (!isSessionEvent(entry)) return;
  invalidateAccountViews(queryClient);
  if (entry.account_id) {
    void queryClient.invalidateQueries({
      queryKey: accountSessionAuditQueryKey({ path: { account_id: entry.account_id } }),
    });
    // The card's "old session closes" line follows the re-key row, not the session list.
    void queryClient.invalidateQueries({
      queryKey: accountRekeyQueryKey({ path: { account_id: entry.account_id } }),
    });
  }
}

// Mounted once on the Accounts page, so a foreign session the watch finds shows up on the
// badge and the open card without a reload.
export function useSessionAuditEvents(): void {
  const queryClient = useQueryClient();
  useLogEventStream((entry) => {
    refreshForSessionEvent(queryClient, entry);
  });
}
