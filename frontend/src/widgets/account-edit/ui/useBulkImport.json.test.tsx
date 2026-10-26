// Pairing the seller's .json with its .session in the add wizard's bulk import.

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

function recordRequests(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => request.url.includes('/2fa/record-json'));
}

const file = (name: string) => new File(['x'], name, { type: 'application/octet-stream' });

function importRequests(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => request.url.includes('/accounts/import-session'));
}

function setup() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return renderHook(() => useBulkImport('session', vi.fn()), { wrapper });
}

test('x.session + x.json become ONE row marked +JSON and send both parts in one request', async () => {
  vi.mocked(fetch).mockResolvedValue(jsonResponse(account('79990001122')));
  const { result } = setup();

  act(() => {
    result.current.add([file('79990001122.json'), file('79990001122.session')]);
  });

  expect(result.current.files).toHaveLength(1);
  expect(result.current.files[0]).toMatchObject({ name: '79990001122.session', withJson: true });
  await waitFor(() => {
    expect(result.current.files[0]?.state).toBe('ok');
  });
  const requests = importRequests();
  expect(requests).toHaveLength(1);
  const form = await requests[0]?.clone().formData();
  expect((form?.get('file') as File).name).toBe('79990001122.session');
  expect((form?.get('json_file') as File).name).toBe('79990001122.json');
  // The import itself records nothing: that waits for the proxy.
  expect(result.current.recordedIds).toEqual([]);
  expect(recordRequests()).toHaveLength(0);
});

test('the pairing is by stem, case-insensitive, and a plain .session sends no json', async () => {
  vi.mocked(fetch).mockImplementation(() => Promise.resolve(jsonResponse(account('a'))));
  const { result } = setup();

  act(() => {
    result.current.add([file('A.session'), file('a.JSON'), file('b.session')]);
  });

  expect(result.current.files.map((f) => [f.name, f.withJson ?? false])).toEqual([
    ['A.session', true],
    ['b.session', false],
  ]);
  await waitFor(() => {
    expect(result.current.files.every((f) => f.state === 'ok')).toBe(true);
  });
  const forms = await Promise.all(importRequests().map((r) => r.clone().formData()));
  expect(forms.filter((form) => form.has('json_file'))).toHaveLength(1);
});

test('a lone .json is a row error that sends nothing and cannot be retried', () => {
  const { result } = setup();

  act(() => {
    result.current.add([file('orphan.json')]);
  });

  expect(result.current.files).toMatchObject([
    { name: 'orphan.json', state: 'error', failure: 'jsonAlone' },
  ]);
  act(() => {
    result.current.retry(result.current.files[0]?.id ?? -1);
  });
  expect(result.current.files[0]?.state).toBe('error');
  expect(importRequests()).toHaveLength(0);
  act(() => {
    result.current.remove(result.current.files[0]?.id ?? -1);
  });
  expect(result.current.files).toHaveLength(0);
});

test('the backend refusals for the json get their own reasons', async () => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({ error: { code: 'bad_request', message: 'session_json_phone_mismatch' } }, 400),
  );
  const { result } = setup();

  act(() => {
    result.current.add([file('1.session'), file('1.json')]);
  });

  await waitFor(() => {
    expect(result.current.files[0]?.failure).toBe('jsonMismatch');
  });
});

test("recordJsonPasswords sends each paired JSON and keeps each row's own outcome", async () => {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname.endsWith('/2fa/record-json')) {
      return pathname.includes('/2/')
        ? jsonResponse(
            { error: { code: 'bad_request', message: 'twofa_current_password_invalid' } },
            400,
          )
        : jsonResponse({ has_stored_password: true });
    }
    const form = await request.formData();
    return jsonResponse(account((form.get('file') as File).name.replace('.session', '')));
  });
  const { result } = setup();
  act(() => {
    result.current.add([file('1.session'), file('1.json'), file('2.session'), file('2.json')]);
    result.current.add([file('3.session')]);
  });
  await waitFor(() => {
    expect(result.current.files.every((f) => f.state === 'ok')).toBe(true);
  });

  await act(async () => {
    await result.current.recordJsonPasswords();
  });

  // A session without a JSON is never sent.
  expect(recordRequests().map((r) => new URL(r.url).pathname)).toEqual([
    '/api/v1/accounts/1/2fa/record-json',
    '/api/v1/accounts/2/2fa/record-json',
  ]);
  expect(result.current.recordedIds).toEqual(['1']);
  expect(result.current.recordFailures).toEqual({ '2': 'twofa_current_password_invalid' });
  expect(result.current.accountIds).toEqual(['1', '2', '3']);
});
