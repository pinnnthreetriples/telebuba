import { act, renderHook } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import { RESUME_WINDOW_MS, useResumableRun } from './useResumableRun';

const KEY = 'telebuba.bulkRun.test';

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

function seed(record: unknown): void {
  window.localStorage.setItem(KEY, JSON.stringify(record));
}

test('progress is written as each account finishes and holds only ids and outcomes', () => {
  const { result } = renderHook(() => useResumableRun('test'));
  act(() => {
    result.current.begin(['a', 'b', 'c']);
    result.current.mark('a', 'done');
    result.current.mark('b', 'failed');
  });
  const stored = JSON.parse(window.localStorage.getItem(KEY) ?? 'null') as Record<string, unknown>;
  expect(stored).toMatchObject({ ids: ['a', 'b', 'c'], done: ['a'], failed: ['b'] });
  expect(Object.keys(stored).sort()).toEqual(['done', 'failed', 'ids', 'runId', 'startedAt']);
});

test('an unfinished run is offered again and resumes with what is left', () => {
  seed({ runId: 'r', startedAt: Date.now(), ids: ['a', 'b', 'c'], done: ['a'], failed: [] });
  const { result } = renderHook(() => useResumableRun('test'));
  expect(result.current.interrupted).toEqual({ processed: 1, total: 3, remaining: ['b', 'c'] });

  let ids: string[] = [];
  act(() => {
    ids = result.current.resume(['a', 'b', 'c']);
  });
  expect(ids).toEqual(['b', 'c']);
  expect(result.current.interrupted).toBeNull();
});

test('ids that no longer exist are skipped on resume', () => {
  seed({ runId: 'r', startedAt: Date.now(), ids: ['a', 'b', 'c'], done: [], failed: [] });
  const { result } = renderHook(() => useResumableRun('test'));
  let ids: string[] = [];
  act(() => {
    ids = result.current.resume(['a', 'c']);
  });
  expect(ids).toEqual(['a', 'c']);
});

test('reset forgets the run for good', () => {
  seed({ runId: 'r', startedAt: Date.now(), ids: ['a', 'b'], done: [], failed: [] });
  const { result, rerender } = renderHook(() => useResumableRun('test'));
  act(() => {
    result.current.reset();
  });
  expect(result.current.interrupted).toBeNull();
  expect(window.localStorage.getItem(KEY)).toBeNull();
  rerender();
  expect(result.current.interrupted).toBeNull();
});

test('a run older than a day is dropped, a younger one is kept', () => {
  const now = 10 * RESUME_WINDOW_MS;
  seed({ runId: 'r', startedAt: now - RESUME_WINDOW_MS - 1, ids: ['a'], done: [], failed: [] });
  const old = renderHook(() => useResumableRun('test', { now: () => now }));
  expect(old.result.current.interrupted).toBeNull();
  expect(window.localStorage.getItem(KEY)).toBeNull();

  seed({ runId: 'r', startedAt: now - RESUME_WINDOW_MS + 1000, ids: ['a'], done: [], failed: [] });
  const young = renderHook(() => useResumableRun('test', { now: () => now }));
  expect(young.result.current.interrupted?.remaining).toEqual(['a']);
});

test('a finished run leaves nothing to resume', () => {
  const first = renderHook(() => useResumableRun('test'));
  act(() => {
    first.result.current.begin(['a']);
    first.result.current.mark('a', 'done');
    first.result.current.finish();
  });
  expect(window.localStorage.getItem(KEY)).toBeNull();

  seed({ runId: 'r', startedAt: Date.now(), ids: ['a'], done: ['a'], failed: [] });
  const again = renderHook(() => useResumableRun('test'));
  expect(again.result.current.interrupted).toBeNull();
});

test('damaged storage content reads as no run', () => {
  window.localStorage.setItem(KEY, '{not json');
  const { result } = renderHook(() => useResumableRun('test'));
  expect(result.current.interrupted).toBeNull();
  seed({ ids: 'nope' });
  expect(renderHook(() => useResumableRun('test')).result.current.interrupted).toBeNull();
});

test('storage that throws never breaks the run', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('full');
  });
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  const { result } = renderHook(() => useResumableRun('test'));
  expect(result.current.interrupted).toBeNull();
  expect(() => {
    act(() => {
      result.current.begin(['a', 'b']);
      result.current.mark('a', 'done');
      result.current.finish();
      result.current.reset();
    });
  }).not.toThrow();
});
