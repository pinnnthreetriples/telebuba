// Live refresh for the warming board. The SSE stream carries every section's events
// (hundreds a day), so the board reacts only to warming ones, coalesces a burst into
// one refresh, never refreshes more than once per THROTTLE_MS, and re-reads only the
// activity logs of the accounts that actually produced an event.
import { type QueryClient, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';

import type { LogEntry } from '@/shared/api';
import { useLogEventStream } from '@/shared/lib';

// The same prefixes the card terminal asks the backend for (WarmingBoard listLogs
// `event_prefix`), so a refresh fires exactly when a terminal could gain a row.
export const WARMING_EVENT_PREFIXES = ['warming_', 'spam_status'] as const;

// Quiet time after the last event before refreshing.
export const DEBOUNCE_MS = 1000;
// Minimum gap between two refreshes; also caps how long a steady stream can hold
// the debounce open, so a busy minute still refreshes every few seconds.
export const THROTTLE_MS = 3000;

// Board-wide queries refreshed on every flush (createQueryKey stamps _id on key[0]).
const BOARD_QUERY_IDS: ReadonlySet<string> = new Set(['getWarmingBoard', 'listWarmingChannels']);
const LOGS_QUERY_ID = 'listLogs';

export function isWarmingEvent(entry: Pick<LogEntry, 'event'>): boolean {
  return WARMING_EVENT_PREFIXES.some((prefix) => entry.event.startsWith(prefix));
}

export interface WarmingRefreshScheduler {
  push(entry: Pick<LogEntry, 'event' | 'account_id'>): void;
  dispose(): void;
}

/**
 * Collect warming events and call `refresh` with the affected account ids once the
 * stream has been quiet for DEBOUNCE_MS, no sooner than THROTTLE_MS after the last
 * refresh and no later than THROTTLE_MS after the first pending event. A pending
 * (trailing) refresh is never dropped, only delayed.
 */
export function createWarmingRefreshScheduler(
  refresh: (accountIds: ReadonlySet<string>) => void,
): WarmingRefreshScheduler {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let pendingIds = new Set<string>();
  let pendingSince: number | null = null;
  let lastRefreshAt = Number.NEGATIVE_INFINITY;

  const flush = () => {
    timer = null;
    const ids = pendingIds;
    pendingIds = new Set();
    pendingSince = null;
    lastRefreshAt = Date.now();
    refresh(ids);
  };

  return {
    push(entry) {
      if (!isWarmingEvent(entry)) return;
      const now = Date.now();
      pendingSince ??= now;
      if (entry.account_id) pendingIds.add(entry.account_id);
      const quietAt = Math.min(now + DEBOUNCE_MS, pendingSince + THROTTLE_MS);
      const dueAt = Math.max(quietAt, lastRefreshAt + THROTTLE_MS);
      if (timer) clearTimeout(timer);
      timer = setTimeout(flush, Math.max(0, dueAt - now));
    },
    dispose() {
      if (timer) clearTimeout(timer);
      timer = null;
    },
  };
}

function queryKeyHead(queryKey: readonly unknown[]): { _id?: string; query?: unknown } {
  return (queryKey[0] ?? {}) as { _id?: string; query?: unknown };
}

/**
 * Refresh the board queries plus the given accounts' activity-log queries, or every
 * account's with 'all' (an operator action, where one extra fetch is cheap).
 */
export function invalidateWarmingQueries(
  queryClient: QueryClient,
  accountIds: ReadonlySet<string> | 'all',
): void {
  void queryClient.invalidateQueries({
    predicate: ({ queryKey }) => {
      const { _id, query } = queryKeyHead(queryKey);
      if (_id == null) return false;
      if (BOARD_QUERY_IDS.has(_id)) return true;
      if (_id !== LOGS_QUERY_ID) return false;
      if (accountIds === 'all') return true;
      const accountId = (query as { account_id?: unknown } | undefined)?.account_id;
      return typeof accountId === 'string' && accountIds.has(accountId);
    },
  });
}

/** Wire the shared SSE stream to the scheduled, account-scoped warming refresh. */
export function useWarmingLiveRefresh(): void {
  const queryClient = useQueryClient();
  const scheduler = useRef<WarmingRefreshScheduler | null>(null);
  useEffect(() => {
    const instance = createWarmingRefreshScheduler((ids) => {
      invalidateWarmingQueries(queryClient, ids);
    });
    scheduler.current = instance;
    return () => {
      instance.dispose();
      scheduler.current = null;
    };
  }, [queryClient]);
  useLogEventStream((entry) => {
    scheduler.current?.push(entry);
  });
}
