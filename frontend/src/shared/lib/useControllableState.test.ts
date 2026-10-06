import { act, renderHook } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import { useControllableState } from './useControllableState';

test('uncontrolled: owns the value, starts from the default and reports each change', () => {
  const onValueChange = vi.fn();
  const { result } = renderHook(() => useControllableState({ defaultValue: 1, onValueChange }));

  expect(result.current[0]).toBe(1);
  act(() => result.current[1](2));
  expect(result.current[0]).toBe(2);
  expect(onValueChange).toHaveBeenLastCalledWith(2);
});

test('controlled: shows the prop, reports the request and waits for the owner', () => {
  const onValueChange = vi.fn();
  const { result, rerender } = renderHook(
    ({ value }: { value: number }) => useControllableState({ value, defaultValue: 0, onValueChange }),
    { initialProps: { value: 5 } },
  );

  act(() => result.current[1](6));
  expect(onValueChange).toHaveBeenCalledWith(6);
  // The owner has not accepted it yet.
  expect(result.current[0]).toBe(5);

  rerender({ value: 6 });
  expect(result.current[0]).toBe(6);
});

test('an equal value is not a change and is not reported', () => {
  const onValueChange = vi.fn();
  const { result } = renderHook(() => useControllableState({ defaultValue: 'a', onValueChange }));

  act(() => result.current[1]('a'));
  expect(onValueChange).not.toHaveBeenCalled();
});

test('a functional update sees the latest value, even twice before a render', () => {
  const { result } = renderHook(() => useControllableState({ defaultValue: 0 }));

  act(() => {
    result.current[1]((n) => n + 1);
    result.current[1]((n) => n + 1);
  });
  expect(result.current[0]).toBe(2);
});

test('the setter is stable across renders', () => {
  const { result, rerender } = renderHook(() => useControllableState({ defaultValue: 0 }));
  const first = result.current[1];
  act(() => first(3));
  rerender();
  expect(result.current[1]).toBe(first);
});
