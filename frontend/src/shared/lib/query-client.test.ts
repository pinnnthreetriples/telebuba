import { useMutation } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import { expect, test, vi } from 'vitest';

import { toastError } from '@/shared/ui';

import { queryClient } from './query-client';

vi.mock('@/shared/ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/ui')>()),
  toastError: vi.fn(),
}));

function wrapper({ children }: { children: ReactNode }) {
  return createElement(QueryClientProvider, { client: queryClient }, children);
}

test('surfaces the API error envelope message when a mutation fails', async () => {
  const { result } = renderHook(
    () =>
      useMutation({
        mutationFn: () => Promise.reject({ error: { code: 'boom', message: 'Nope, failed' } }),
      }),
    { wrapper },
  );
  result.current.mutate(undefined);
  await waitFor(() => {
    expect(toastError).toHaveBeenCalledWith('Nope, failed');
  });
});

test('falls back to a translated message when the envelope has none', async () => {
  vi.mocked(toastError).mockClear();
  const { result } = renderHook(
    () =>
      useMutation({
        mutationFn: () => Promise.reject(new Error('network down')),
      }),
    { wrapper },
  );
  result.current.mutate(undefined);
  await waitFor(() => {
    expect(toastError).toHaveBeenCalledOnce();
  });
  // Not the raw Error — a user-facing fallback string.
  expect(vi.mocked(toastError).mock.calls[0]?.[0]).not.toBe('network down');
});

test('does not toast on unauthorized (the query cache redirects instead)', async () => {
  vi.mocked(toastError).mockClear();
  const { result } = renderHook(
    () =>
      useMutation({
        mutationFn: () => Promise.reject({ error: { code: 'unauthorized' } }),
      }),
    { wrapper },
  );
  result.current.mutate(undefined);
  await waitFor(() => {
    expect(result.current.isError).toBe(true);
  });
  expect(toastError).not.toHaveBeenCalled();
});
