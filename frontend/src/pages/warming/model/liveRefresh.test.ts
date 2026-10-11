import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import {
  createWarmingRefreshScheduler,
  DEBOUNCE_MS,
  invalidateWarmingQueries,
  isWarmingEvent,
  THROTTLE_MS,
} from './liveRefresh';

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

function event(name: string, accountId: string | null = 'acc-1') {
  return { event: name, account_id: accountId };
}

test('only warming_* and spam_status* events count as warming events', () => {
  expect(isWarmingEvent(event('warming_cycle_completed'))).toBe(true);
  expect(isWarmingEvent(event('spam_status_refreshed'))).toBe(true);
  expect(isWarmingEvent(event('neurocomment_posted'))).toBe(false);
  expect(isWarmingEvent(event('telegram_pool_connect_retry'))).toBe(false);
  expect(isWarmingEvent(event('cycle_started'))).toBe(false);
});

test('non-warming events never trigger a refresh', () => {
  const refresh = vi.fn();
  const scheduler = createWarmingRefreshScheduler(refresh);
  scheduler.push(event('neurocomment_posted'));
  scheduler.push(event('masstagging_tagged'));
  vi.advanceTimersByTime(THROTTLE_MS * 3);
  expect(refresh).not.toHaveBeenCalled();
});

test('a burst of warming events coalesces into one refresh after the quiet window', () => {
  const refresh = vi.fn();
  const scheduler = createWarmingRefreshScheduler(refresh);
  scheduler.push(event('warming_started', 'acc-1'));
  vi.advanceTimersByTime(200);
  scheduler.push(event('warming_dialogue_reply', 'acc-2'));
  vi.advanceTimersByTime(200);
  scheduler.push(event('spam_status_refreshed', null));
  vi.advanceTimersByTime(DEBOUNCE_MS - 1);
  expect(refresh).not.toHaveBeenCalled();
  vi.advanceTimersByTime(1);
  expect(refresh).toHaveBeenCalledTimes(1);
  expect([...(refresh.mock.calls[0]![0] as Set<string>)].sort()).toEqual(['acc-1', 'acc-2']);
});

test('refreshes at most once per throttle window and keeps the trailing call', () => {
  const refresh = vi.fn();
  const scheduler = createWarmingRefreshScheduler(refresh);
  scheduler.push(event('warming_started', 'acc-1'));
  vi.advanceTimersByTime(DEBOUNCE_MS);
  expect(refresh).toHaveBeenCalledTimes(1); // t = 1000

  scheduler.push(event('warming_stopped', 'acc-2')); // quiet at 2000, throttled to 4000
  vi.advanceTimersByTime(DEBOUNCE_MS);
  expect(refresh).toHaveBeenCalledTimes(1);
  vi.advanceTimersByTime(THROTTLE_MS - DEBOUNCE_MS - 1);
  expect(refresh).toHaveBeenCalledTimes(1);
  vi.advanceTimersByTime(1);
  expect(refresh).toHaveBeenCalledTimes(2);
  expect([...(refresh.mock.calls[1]![0] as Set<string>)]).toEqual(['acc-2']);
});

test('a steady stream still refreshes every throttle window instead of starving', () => {
  const refresh = vi.fn();
  const scheduler = createWarmingRefreshScheduler(refresh);
  for (let elapsed = 0; elapsed < THROTTLE_MS * 3; elapsed += 500) {
    scheduler.push(event('warming_dialogue_reply'));
    vi.advanceTimersByTime(500);
  }
  expect(refresh).toHaveBeenCalledTimes(3);
});

test('dispose drops a pending refresh', () => {
  const refresh = vi.fn();
  const scheduler = createWarmingRefreshScheduler(refresh);
  scheduler.push(event('warming_started'));
  scheduler.dispose();
  vi.advanceTimersByTime(THROTTLE_MS * 2);
  expect(refresh).not.toHaveBeenCalled();
});

function seededClient(): QueryClient {
  const client = new QueryClient();
  const keys = [
    [{ _id: 'getWarmingBoard' }],
    [{ _id: 'listWarmingChannels' }],
    [{ _id: 'listLogs', query: { account_id: 'acc-1', event_prefix: 'warming_,spam_status' } }],
    [{ _id: 'listLogs', query: { account_id: 'acc-2', event_prefix: 'warming_,spam_status' } }],
    [{ _id: 'listLogs', query: { event_prefix: 'neurocomment' } }],
    [{ _id: 'listAccounts' }],
  ];
  for (const key of keys) client.setQueryData(key, { ok: true });
  return client;
}

function invalidatedIds(client: QueryClient): string[] {
  return client
    .getQueryCache()
    .getAll()
    .filter((query) => query.state.isInvalidated)
    .map((query) => {
      const head = query.queryKey[0] as { _id: string; query?: { account_id?: string } };
      return head.query?.account_id ? `${head._id}:${head.query.account_id}` : head._id;
    })
    .sort();
}

test("invalidates the board queries and only the affected account's logs", () => {
  const client = seededClient();
  invalidateWarmingQueries(client, new Set(['acc-1']));
  expect(invalidatedIds(client)).toEqual([
    'getWarmingBoard',
    'listLogs:acc-1',
    'listWarmingChannels',
  ]);
});

test("an operator action ('all') invalidates every log query alongside the board", () => {
  const client = seededClient();
  invalidateWarmingQueries(client, 'all');
  expect(invalidatedIds(client)).toEqual([
    'getWarmingBoard',
    'listLogs',
    'listLogs:acc-1',
    'listLogs:acc-2',
    'listWarmingChannels',
  ]);
});
