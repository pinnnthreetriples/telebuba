import { renderHook } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import { useLogEventStream } from './useLogEventStream';

interface MockSource {
  onmessage: ((event: MessageEvent) => void) | null;
  readyState: number;
  emit(data: unknown): void;
}
interface MockSourceCtor {
  instances: MockSource[];
  last(): MockSource | undefined;
}

const Sources = globalThis.EventSource as unknown as MockSourceCtor;

test('delivers parsed entries to the callback', () => {
  const onEntry = vi.fn();
  renderHook(() => {
    useLogEventStream(onEntry);
  });
  Sources.last()?.emit({ id: 1, event: 'live_x' });
  expect(onEntry).toHaveBeenCalledWith(expect.objectContaining({ id: 1, event: 'live_x' }));
});

test('ignores malformed frames', () => {
  const onEntry = vi.fn();
  renderHook(() => {
    useLogEventStream(onEntry);
  });
  const source = Sources.last();
  source?.onmessage?.(new MessageEvent('message', { data: 'not json' }));
  expect(onEntry).not.toHaveBeenCalled();
});

test('closes the connection on unmount', () => {
  const { unmount } = renderHook(() => {
    useLogEventStream(vi.fn());
  });
  const source = Sources.last();
  unmount();
  expect(source?.readyState).toBe(2);
});

test('uses the latest callback without reconnecting', () => {
  const first = vi.fn();
  const second = vi.fn();
  const { rerender } = renderHook(({ cb }) => useLogEventStream(cb), {
    initialProps: { cb: first },
  });
  const count = Sources.instances.length;
  rerender({ cb: second });
  expect(Sources.instances.length).toBe(count); // no new EventSource opened
  Sources.last()?.emit({ id: 2, event: 'again' });
  expect(second).toHaveBeenCalledOnce();
  expect(first).not.toHaveBeenCalled();
});
