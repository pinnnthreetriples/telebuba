import { act, renderHook } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import { useTransientFeedback } from './useTransientFeedback';

test('marks a key ok, then clears it after the delay', () => {
  vi.useFakeTimers();
  const { result } = renderHook(() => useTransientFeedback(1000));
  act(() => {
    result.current.mark('a1', true);
  });
  expect(result.current.feedback).toEqual({ a1: 'ok' });

  act(() => {
    vi.advanceTimersByTime(1000);
  });
  expect(result.current.feedback).toEqual({});
  vi.useRealTimers();
});

test('a pending auto-clear timer does not outlive the component', () => {
  vi.useFakeTimers();
  const { result, unmount } = renderHook(() => useTransientFeedback(1000));
  act(() => {
    result.current.mark('a1', true);
  });

  // The account-edit delete calls onBack(), which unmounts the whole tree while
  // an auto-clear timer is still pending — it then fired against a dead tree.
  unmount();

  expect(vi.getTimerCount()).toBe(0);
  vi.useRealTimers();
});

test('marks a key err on failure', () => {
  const { result } = renderHook(() => useTransientFeedback());
  act(() => {
    result.current.mark('a1', false);
  });
  expect(result.current.feedback).toEqual({ a1: 'err' });
});
