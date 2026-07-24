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

test('translates a stable media code in the mutation toast', async () => {
  vi.mocked(toastError).mockClear();
  const { result } = renderHook(
    () =>
      useMutation({
        mutationFn: () =>
          Promise.reject({
            error: { code: 'bad_request', message: 'profile_photo_stale_reference' },
          }),
      }),
    { wrapper },
  );
  result.current.mutate(undefined);
  await waitFor(() => {
    // The operator sees the translated copy, not the raw stable code.
    expect(toastError).toHaveBeenCalledWith('Фото изменилось на Telegram — обновите список');
  });
});

test('a flood_wait toast carries the retry-after seconds (string on the wire)', async () => {
  vi.mocked(toastError).mockClear();
  const { result } = renderHook(
    () =>
      useMutation({
        mutationFn: () =>
          Promise.reject({
            error: {
              code: 'bad_request',
              message: 'flood_wait',
              // The backend serialises envelope fields as strings.
              fields: { retry_after_seconds: '345' },
            },
          }),
      }),
    { wrapper },
  );
  result.current.mutate(undefined);
  await waitFor(() => {
    expect(toastError).toHaveBeenCalledWith('Telegram ограничил действия — повторите через 345 с');
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

test('redirects a mutation-only unauthorized to /login without toasting', async () => {
  vi.mocked(toastError).mockClear();
  const assign = vi.spyOn(window.location, 'assign').mockImplementation(() => {});
  const { result } = renderHook(
    () =>
      useMutation({
        mutationFn: () => Promise.reject({ error: { code: 'unauthorized' } }),
      }),
    { wrapper },
  );
  result.current.mutate(undefined);
  await waitFor(() => {
    expect(assign).toHaveBeenCalledWith('/login');
  });
  expect(toastError).not.toHaveBeenCalled();
  assign.mockRestore();
});
