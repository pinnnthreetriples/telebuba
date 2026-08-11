import { useMutation } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import { expect, test, vi } from 'vitest';

import { toastError } from '@/shared/ui';

import { mutationErrorText, queryClient } from './query-client';

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

test('translates shared API protection codes instead of exposing internals', () => {
  expect(
    mutationErrorText({
      error: { code: 'too_many_requests', message: 'upload_capacity_exceeded' },
    }),
  ).toBe('Слишком много одновременных загрузок. Повторите попытку чуть позже.');
  expect(mutationErrorText({ error: { code: 'forbidden', message: 'untrusted_origin' } })).toBe(
    'Запрос пришёл с недоверенного сайта. Перезагрузите приложение и попробуйте снова.',
  );
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

test('translates a story code in the toast, not just inline in the modal', async () => {
  vi.mocked(toastError).mockClear();
  const { result } = renderHook(
    () =>
      useMutation({
        mutationFn: () =>
          Promise.reject({
            error: { code: 'bad_request', message: 'story_video_ffmpeg_missing' },
          }),
      }),
    { wrapper },
  );
  result.current.mutate(undefined);
  await waitFor(() => {
    // This toast fires for EVERY mutation including a story publish, so the
    // addStory table has to be in its fallback chain — otherwise the operator
    // reads correct copy inline and the raw code in the toast beside it.
    expect(toastError).toHaveBeenCalledWith(
      'На сервере нет ffmpeg — обрабатывать видео нечем. Это настройка сервера, а не проблема файла',
    );
  });
});

test('a slow-mode refusal reads as copy with its retry-after seconds', async () => {
  vi.mocked(toastError).mockClear();
  const { result } = renderHook(
    () =>
      useMutation({
        mutationFn: () =>
          Promise.reject({
            error: {
              code: 'bad_request',
              message: 'slow_mode_wait',
              fields: { retry_after_seconds: '30' },
            },
          }),
      }),
    { wrapper },
  );
  result.current.mutate(undefined);
  await waitFor(() => {
    expect(toastError).toHaveBeenCalledWith(
      'В канале включён медленный режим — повторите через 30 с',
    );
  });
});
