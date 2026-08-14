import { renderHook } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import { useLogEventStream } from './useLogEventStream';

// Its own file on purpose: "the session is dead" is module-level state that must
// survive an unmount (that is the whole point), so it cannot be undone between
// tests in a shared file. Vitest isolates modules per file, which resets it here.

interface MockSource {
  readyState: number;
  emitNamed(type: string, data?: unknown): void;
}
interface MockSourceCtor {
  instances: MockSource[];
  last(): MockSource | undefined;
}

const Sources = globalThis.EventSource as unknown as MockSourceCtor;

test('a revoked session closes the stream instead of reconnecting forever', () => {
  const onStatus = vi.fn();
  const first = renderHook(() => {
    useLogEventStream(() => {}, onStatus);
  });
  expect(Sources.instances).toHaveLength(1);

  Sources.last()?.emitNamed('session-invalid');

  // Closed deliberately, and the operator's pill reflects it.
  expect(Sources.last()?.readyState).toBe(2);
  expect(onStatus).toHaveBeenCalledWith('error');

  // The tab that most needs this is one nobody is watching, so the real test is
  // that nothing opens a replacement — an EventSource left to itself would retry
  // every few seconds for the lifetime of the tab.
  first.unmount();
  renderHook(() => {
    useLogEventStream(() => {});
  });

  expect(Sources.instances).toHaveLength(1);
});
