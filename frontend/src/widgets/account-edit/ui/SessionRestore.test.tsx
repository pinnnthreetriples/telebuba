import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead, SessionCopies } from '@/shared/api';

import { SessionRestore } from './SessionRestore';

const COPIES = '/api/v1/accounts/acc-1/session/copies';
const RESTORE = '/api/v1/accounts/acc-1/session/restore';
const ACCOUNT = { account_id: 'acc-1', status: 'session_file_missing' } as AccountRead;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function serve(copies: SessionCopies): void {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const path = new URL(request.url).pathname;
    if (path === COPIES) return Promise.resolve(json(copies));
    if (path === RESTORE) return Promise.resolve(json({ ...ACCOUNT, status: 'alive' }));
    return Promise.resolve(json({}));
  });
}

function calls(path: string, method: string): number {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => new URL(request.url).pathname === path && request.method === method)
    .length;
}

function renderRestore() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <SessionRestore account={ACCOUNT} />
    </QueryClientProvider>,
  );
}

test('offers the restore only when the backend says it can work, and posts it once', async () => {
  serve({
    vault_enabled: true,
    count: 2,
    newest_at: '2026-11-20T10:00:00+00:00',
    can_restore: true,
  });
  renderRestore();

  const button = await screen.findByRole('button', { name: 'Восстановить из копии' });
  expect(calls(RESTORE, 'POST')).toBe(0);
  await userEvent.click(button);

  await waitFor(() => {
    expect(calls(RESTORE, 'POST')).toBe(1);
  });
});

test('says nothing when there is no copy to restore from', async () => {
  serve({ vault_enabled: true, count: 0, newest_at: null, can_restore: false });
  renderRestore();

  await waitFor(() => {
    expect(calls(COPIES, 'GET')).toBe(1);
  });
  expect(screen.queryByTestId('session-restore')).toBeNull();
});
