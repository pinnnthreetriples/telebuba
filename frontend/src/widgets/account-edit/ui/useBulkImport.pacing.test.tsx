import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { expect, test, vi } from 'vitest';

import {
  IMPORT_PAUSE_MAX_MS,
  IMPORT_PAUSE_MIN_MS,
  jitteredPause,
  spreadByProxy,
} from './importPacing';
import { type ImportPacing, useBulkImport } from './useBulkImport';

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function file(name: string): File {
  return new File(['x'], name, { type: 'application/octet-stream' });
}

function setup(pacing: ImportPacing) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return renderHook(() => useBulkImport('session', vi.fn(), 'desktop', '', pacing), { wrapper });
}

// The order the files reached the server, from their names in the multipart body.
async function sentOrder(): Promise<string[]> {
  const names: string[] = [];
  for (const [input] of vi.mocked(fetch).mock.calls) {
    const form = await (input as Request).clone().formData();
    names.push((form.get('file') as File).name);
  }
  return names;
}

test('the pause is drawn between 1.5 and 4 seconds', () => {
  expect(jitteredPause(() => 0)).toBe(IMPORT_PAUSE_MIN_MS);
  expect(jitteredPause(() => 1)).toBe(IMPORT_PAUSE_MAX_MS);
  expect(jitteredPause(() => 0.5)).toBe(2750);
});

test('consecutive imports wait a fresh pause each, and the first does not wait', async () => {
  let n = 0;
  vi.mocked(fetch).mockReset();
  vi.mocked(fetch).mockImplementation(() => {
    n += 1;
    return Promise.resolve(
      jsonResponse({ account_id: `acc-${n}`, status: 'new', created_at: 'n', updated_at: 'n' }),
    );
  });
  const pauses = [1600, 3900];
  const waited: number[] = [];
  const { result } = setup({
    pause: () => pauses[waited.length] ?? 0,
    sleep: (ms) => {
      waited.push(ms);
      return Promise.resolve();
    },
  });

  act(() => {
    result.current.add([file('a.session'), file('b.session'), file('c.session')]);
  });
  await waitFor(() => {
    expect(result.current.files.every((f) => f.state === 'ok')).toBe(true);
  });

  expect(waited).toEqual([1600, 3900]);
});

test('nothing is sent once a reset lands during the pause', async () => {
  vi.mocked(fetch).mockReset();
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(
      jsonResponse({ account_id: 'acc-1', status: 'new', created_at: 'n', updated_at: 'n' }),
    ),
  );
  let release: () => void = () => undefined;
  const { result } = setup({
    pause: () => 2000,
    sleep: () =>
      new Promise<void>((resolve) => {
        release = resolve;
      }),
  });

  act(() => {
    result.current.add([file('a.session'), file('b.session')]);
  });
  await waitFor(() => {
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  });
  act(() => {
    result.current.reset();
  });
  release();
  await new Promise((resolve) => setTimeout(resolve, 20));

  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
});

test('files that share a proxy are not sent back to back when another order exists', async () => {
  vi.mocked(fetch).mockReset();
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(
      jsonResponse({ account_id: 'acc', status: 'new', created_at: 'n', updated_at: 'n' }),
    ),
  );
  const proxies: Record<string, string> = {
    'a1.session': 'p1',
    'a2.session': 'p1',
    'b1.session': 'p2',
    'a3.session': 'p1',
  };
  const { result } = setup({
    pause: () => 0,
    proxyOf: (picked) => proxies[picked.name],
  });

  act(() => {
    result.current.add(Object.keys(proxies).map(file));
  });
  await waitFor(() => {
    expect(result.current.files.every((f) => f.state === 'ok')).toBe(true);
  });

  expect(await sentOrder()).toEqual(['a1.session', 'b1.session', 'a2.session', 'a3.session']);
});

test('spreadByProxy keeps the order when nothing conflicts or nothing can help', () => {
  const proxyOf = (id: number) => ['x', 'y', 'z'][id];
  expect(spreadByProxy([0, 1, 2], proxyOf)).toEqual([0, 1, 2]);
  // All on one proxy: no other order exists, so none is invented.
  expect(spreadByProxy([0, 1, 2], () => 'same')).toEqual([0, 1, 2]);
  // Unknown proxies never conflict.
  expect(spreadByProxy([0, 1, 2], () => undefined)).toEqual([0, 1, 2]);
  // The import that went last counts as the neighbour of the first.
  expect(spreadByProxy([0, 1], (id) => ['p', 'q'][id], 'p')).toEqual([1, 0]);
  expect(spreadByProxy([], proxyOf)).toEqual([]);
});
