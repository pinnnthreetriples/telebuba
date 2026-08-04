import { isRedirect } from '@tanstack/react-router';
import { expect, test, vi } from 'vitest';

import { queryClient } from '@/shared/lib';

import { ensureSession } from './router';

// A fresh Response per call: the guard's query retries once and a Response body can
// only be read one time.
function respond(body: unknown, status: number): void {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  );
}

async function guardError(): Promise<unknown> {
  return ensureSession().then(
    () => null,
    (error: unknown) => error,
  );
}

test('a dead session redirects to the login screen', async () => {
  queryClient.clear();
  const assign = vi.spyOn(window.location, 'assign').mockImplementation(() => undefined);
  respond({ error: { code: 'unauthorized', message: 'no session' } }, 401);

  const error = await guardError();

  expect(isRedirect(error)).toBe(true);
  expect(error).toMatchObject({ options: { to: '/login' } });
  assign.mockRestore();
});

test('a backend failure is an error, not a logout', async () => {
  queryClient.clear();
  respond({ error: { code: 'internal_error', message: 'boom' } }, 500);

  const error = await guardError();

  // The old guard swallowed every failure and redirected, so a 500, a dropped
  // connection or a timeout was indistinguishable from a real logout — and the
  // login screen has nothing to say about a backend that is down.
  expect(isRedirect(error)).toBe(false);
  expect(error).toMatchObject({ error: { code: 'internal_error' } });
});

test('the guard resolves for a live session', async () => {
  queryClient.clear();
  respond({ id: 'u1', username: 'admin', role: 'admin' }, 200);

  await expect(ensureSession()).resolves.toBeUndefined();
});
