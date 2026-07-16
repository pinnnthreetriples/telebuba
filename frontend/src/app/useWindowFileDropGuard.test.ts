import { renderHook } from '@testing-library/react';
import { expect, test } from 'vitest';

import { useWindowFileDropGuard } from './useWindowFileDropGuard';

function dispatch(type: string): Event {
  const event = new Event(type, { bubbles: true, cancelable: true });
  window.dispatchEvent(event);
  return event;
}

test('cancels window dragover and drop so a stray file drop cannot navigate', () => {
  const { unmount } = renderHook(() => useWindowFileDropGuard());
  expect(dispatch('dragover').defaultPrevented).toBe(true);
  expect(dispatch('drop').defaultPrevented).toBe(true);

  // The listeners are removed on unmount.
  unmount();
  expect(dispatch('dragover').defaultPrevented).toBe(false);
  expect(dispatch('drop').defaultPrevented).toBe(false);
});
