import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { expect, test, vi } from 'vitest';

import { useBulkImport } from './useBulkImport';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function account(id: string) {
  return { account_id: id, status: 'new', created_at: 'n', updated_at: 'n' };
}

function file(name: string): File {
  return new File(['x'], name, { type: 'application/octet-stream' });
}

function calls(fragment: string): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => request.url.includes(fragment));
}

function setup(method: 'session' | 'tdata') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  const onSettledOne = vi.fn();
  const hook = renderHook(() => useBulkImport(method, onSettledOne), { wrapper });
  return { ...hook, onSettledOne };
}

test('two session files → two import-session requests, both adopted', async () => {
  let n = 0;
  vi.mocked(fetch).mockImplementation(() => {
    n += 1;
    return Promise.resolve(jsonResponse(account(`acc-${n}`)));
  });
  const { result, onSettledOne } = setup('session');

  act(() => {
    result.current.add([file('a.session'), file('b.session')]);
  });
  expect(result.current.importing).toBe(true);
  expect(result.current.files.map((f) => f.name)).toEqual(['a.session', 'b.session']);

  await waitFor(() => {
    expect(result.current.files.every((f) => f.state === 'ok')).toBe(true);
  });
  expect(calls('/accounts/import-session')).toHaveLength(2);
  expect(result.current.accountIds.sort()).toEqual(['acc-1', 'acc-2']);
  expect(result.current.importing).toBe(false);
  expect(onSettledOne).toHaveBeenCalledTimes(2);
});

test('a tdata zip holding two accounts adopts both ids', async () => {
  vi.mocked(fetch).mockResolvedValue(jsonResponse({ accounts: [account('one'), account('two')] }));
  const { result } = setup('tdata');

  act(() => {
    result.current.add([file('tdata.zip')]);
  });
  await waitFor(() => {
    expect(result.current.files[0]?.state).toBe('ok');
  });
  expect(calls('/accounts/import-tdata')).toHaveLength(1);
  expect(result.current.accountIds).toEqual(['one', 'two']);
});

test('a failing file lands in error; retry re-sends it and turns ok', async () => {
  let fail = true;
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(fail ? jsonResponse({ detail: 'boom' }, 500) : jsonResponse(account('ok'))),
  );
  const { result, onSettledOne } = setup('session');

  act(() => {
    result.current.add([file('a.session')]);
  });
  await waitFor(() => {
    expect(result.current.files[0]?.state).toBe('error');
  });
  expect(result.current.accountIds).toEqual([]);
  expect(onSettledOne).toHaveBeenCalledTimes(1);

  fail = false;
  act(() => {
    result.current.retry(0);
  });
  expect(result.current.files[0]?.state).toBe('importing');
  await waitFor(() => {
    expect(result.current.files[0]?.state).toBe('ok');
  });
  expect(calls('/accounts/import-session')).toHaveLength(2);
  expect(result.current.accountIds).toEqual(['ok']);
  expect(onSettledOne).toHaveBeenCalledTimes(2);
});

test('at most two files are in flight at once', async () => {
  const pending: ((response: Response) => void)[] = [];
  vi.mocked(fetch).mockImplementation(
    () =>
      new Promise((resolve) => {
        pending.push(resolve);
      }),
  );
  const { result } = setup('session');

  act(() => {
    result.current.add([file('a.session'), file('b.session'), file('c.session')]);
  });
  await waitFor(() => {
    expect(pending).toHaveLength(2);
  });
  // Give the third every chance to start; it must not.
  await act(async () => {
    await Promise.resolve();
  });
  expect(pending).toHaveLength(2);

  pending[0]?.(jsonResponse(account('a')));
  await waitFor(() => {
    expect(pending).toHaveLength(3);
  });
  expect(result.current.files.filter((f) => f.state === 'ok')).toHaveLength(1);
});

test('reset clears the list; a file settling afterwards is not adopted but still refetches', async () => {
  let resolveImport!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveImport = resolve;
      }),
  );
  const { result, onSettledOne } = setup('session');

  act(() => {
    result.current.add([file('a.session')]);
  });
  await waitFor(() => {
    expect(resolveImport).toBeDefined();
  });
  act(() => {
    result.current.reset();
  });
  expect(result.current.files).toEqual([]);
  expect(result.current.importing).toBe(false);

  // The account was created server-side regardless: the table has to refetch,
  // but the wizard that has moved on must not be re-provisioned with it.
  resolveImport(jsonResponse(account('late')));
  await waitFor(() => {
    expect(onSettledOne).toHaveBeenCalledTimes(1);
  });
  expect(result.current.files).toEqual([]);
  expect(result.current.accountIds).toEqual([]);

  // The pool is free again after the reset: a new pick starts at once.
  act(() => {
    result.current.add([file('b.session')]);
  });
  await waitFor(() => {
    expect(calls('/accounts/import-session')).toHaveLength(2);
  });
});
