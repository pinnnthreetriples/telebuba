import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { useLogEventStream } from './useLogEventStream';

interface MockSource {
  onmessage: ((event: MessageEvent) => void) | null;
  readyState: number;
  OPEN: number;
  emit(data: unknown): void;
  emitOpen(): void;
  emitError(): void;
}
interface MockSourceCtor {
  instances: MockSource[];
  last(): MockSource | undefined;
}

const Sources = globalThis.EventSource as unknown as MockSourceCtor;

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.runOnlyPendingTimers();
  vi.useRealTimers();
});

// Flush the trailing debounce window.
function flush() {
  act(() => {
    vi.runAllTimers();
  });
}

test('delivers parsed entries to the callback after the debounce', () => {
  const onEntry = vi.fn();
  renderHook(() => {
    useLogEventStream(onEntry);
  });
  Sources.last()?.emit({ id: 1, event: 'live_x' });
  expect(onEntry).not.toHaveBeenCalled(); // debounced, not yet delivered
  flush();
  expect(onEntry).toHaveBeenCalledWith(expect.objectContaining({ id: 1, event: 'live_x' }));
});

test('shares a single EventSource across multiple hook mounts', () => {
  const before = Sources.instances.length;
  const a = renderHook(() => {
    useLogEventStream(vi.fn());
  });
  const b = renderHook(() => {
    useLogEventStream(vi.fn());
  });
  // Two subscribers, but only one EventSource opened.
  expect(Sources.instances.length).toBe(before + 1);
  a.unmount();
  b.unmount();
});

test('fans one delivery out to every subscriber', () => {
  const first = vi.fn();
  const second = vi.fn();
  const a = renderHook(() => {
    useLogEventStream(first);
  });
  const b = renderHook(() => {
    useLogEventStream(second);
  });
  Sources.last()?.emit({ id: 7, event: 'shared' });
  flush();
  expect(first).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }));
  expect(second).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }));
  a.unmount();
  b.unmount();
});

test('coalesces a burst into a single flush while preserving every entry', () => {
  const onEntry = vi.fn();
  const { unmount } = renderHook(() => {
    useLogEventStream(onEntry);
  });
  const source = Sources.last();
  source?.emit({ id: 1, event: 'a' });
  source?.emit({ id: 2, event: 'b' });
  source?.emit({ id: 3, event: 'c' });
  expect(onEntry).not.toHaveBeenCalled(); // still within one window
  flush();
  expect(onEntry).toHaveBeenCalledTimes(3); // every buffered entry, one burst
  unmount();
});

test('ignores malformed frames', () => {
  const onEntry = vi.fn();
  const { unmount } = renderHook(() => {
    useLogEventStream(onEntry);
  });
  const source = Sources.last();
  source?.onmessage?.(new MessageEvent('message', { data: 'not json' }));
  flush();
  expect(onEntry).not.toHaveBeenCalled();
  unmount();
});

test('closes the shared connection when the last subscriber unmounts', () => {
  const { unmount } = renderHook(() => {
    useLogEventStream(vi.fn());
  });
  const source = Sources.last();
  unmount();
  expect(source?.readyState).toBe(2);
});

test('clears a pending flush when the last subscriber unmounts', () => {
  const onEntry = vi.fn();
  const { unmount } = renderHook(() => {
    useLogEventStream(onEntry);
  });
  Sources.last()?.emit({ id: 9, event: 'pending' }); // schedules a flush
  unmount(); // closes the source and clears the timer before it fires
  flush();
  expect(onEntry).not.toHaveBeenCalled();
});

test('reports connection status transitions immediately (not debounced)', () => {
  const onStatus = vi.fn();
  const { unmount } = renderHook(() => {
    useLogEventStream(vi.fn(), onStatus);
  });
  expect(onStatus).toHaveBeenCalledWith('connecting');
  act(() => {
    Sources.last()?.emitOpen();
  });
  expect(onStatus).toHaveBeenCalledWith('open');
  act(() => {
    Sources.last()?.emitError();
  });
  expect(onStatus).toHaveBeenCalledWith('error');
  unmount();
});

test('uses the latest callback without resubscribing', () => {
  const first = vi.fn();
  const second = vi.fn();
  const { rerender, unmount } = renderHook(({ cb }) => useLogEventStream(cb), {
    initialProps: { cb: first },
  });
  const count = Sources.instances.length;
  rerender({ cb: second });
  expect(Sources.instances.length).toBe(count); // no new EventSource opened
  Sources.last()?.emit({ id: 2, event: 'again' });
  flush();
  expect(second).toHaveBeenCalledOnce();
  expect(first).not.toHaveBeenCalled();
  unmount();
});
