import { renderHook } from '@testing-library/react';
import { StrictMode } from 'react';
import { expect, test } from 'vitest';

import { useMountedRef } from './useMountedRef';

test('reads mounted under StrictMode, whose extra cleanup must not stick', () => {
  const { result, unmount } = renderHook(() => useMountedRef(), { wrapper: StrictMode });

  expect(result.current.current).toBe(true);
  unmount();
  expect(result.current.current).toBe(false);
});
