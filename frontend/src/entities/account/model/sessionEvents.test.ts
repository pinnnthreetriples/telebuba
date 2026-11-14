import { QueryClient } from '@tanstack/react-query';
import { expect, test, vi } from 'vitest';

import { accountRekeyQueryKey, accountSessionAuditQueryKey } from '../api/accounts.queries';
import { isSessionEvent, refreshForSessionEvent } from './sessionEvents';

function clientSpy() {
  const queryClient = new QueryClient();
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries').mockResolvedValue();
  return { queryClient, invalidate };
}

test("a session event refreshes the list and that account's Sessions card", () => {
  const { queryClient, invalidate } = clientSpy();

  refreshForSessionEvent(queryClient, {
    event: 'session_audit_foreign_found',
    account_id: 'acc-1',
  });

  const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey);
  expect(keys).toContainEqual(accountSessionAuditQueryKey({ path: { account_id: 'acc-1' } }));
  // The list, the stats and the proxies refresh with it.
  expect(keys.length).toBeGreaterThan(1);
});

test('an event without an account refreshes the views but no card', () => {
  const { queryClient, invalidate } = clientSpy();

  refreshForSessionEvent(queryClient, { event: 'session_audit_completed', account_id: null });

  const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey);
  expect(keys).not.toContainEqual(accountSessionAuditQueryKey({ path: { account_id: 'acc-1' } }));
  expect(keys.length).toBeGreaterThan(0);
});

test('any other event refreshes nothing', () => {
  const { queryClient, invalidate } = clientSpy();

  refreshForSessionEvent(queryClient, { event: 'warming_started', account_id: 'acc-1' });

  expect(invalidate).not.toHaveBeenCalled();
  expect(isSessionEvent({ event: 'session_rekeyed' })).toBe(true);
  expect(isSessionEvent({ event: 'passkey_created' })).toBe(false);
});

test.each([
  'session_old_key_pending',
  'session_old_key_closed',
  'session_old_key_resolved',
  'session_old_key_problem',
])("%s refreshes that account's re-key card", (event) => {
  const { queryClient, invalidate } = clientSpy();

  refreshForSessionEvent(queryClient, { event, account_id: 'acc-1' });

  const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey);
  expect(keys).toContainEqual(accountRekeyQueryKey({ path: { account_id: 'acc-1' } }));
});
