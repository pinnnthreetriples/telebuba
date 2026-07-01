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

test('marks a key err on failure', () => {
  const { result } = renderHook(() => useTransientFeedback());
  act(() => {
    result.current.mark('a1', false);
  });
  expect(result.current.feedback).toEqual({ a1: 'err' });
});
