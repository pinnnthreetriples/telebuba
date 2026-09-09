import { useMutation } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { updateAccountProfileMutation } from '@/entities/account';
import type { AccountProfileUpdateRequest } from '@/shared/api';

export type BulkProfileRow = {
  accountId: string;
  state: 'queued' | 'running' | 'ok' | 'error';
  error: unknown;
};

/** What the bulk form applies: the profile request minus the account it targets. */
export type BulkProfileFields = Omit<AccountProfileUpdateRequest, 'account_id'>;

// The bulk profile save: the same text applied to each selected account, one
// account at a time, in list order.
//
// STRICTLY sequential, like useBulkTwofa and unlike useBulkImport's two-at-a-time
// pump. Not because the sessions share anything — they don't — but because
// `update_profile` is a flood-sensitive action whose FloodWait writes the sticky
// `flood_wait` status (services/accounts/profile.py), and a fleet-wide fan-out
// turns one rate-limited account into seventeen. ponytail: one at a time; raise
// the width here if a large fleet ever makes the wall-clock the complaint.
//
// `mutateAsync` in a plain `for`, never `.mutate` per row: one useMutation
// observer is a single callback slot (and the ESLint rule that says so).
export function useBulkProfile() {
  const [rows, setRows] = useState<BulkProfileRow[]>([]);
  // Bumped by a new run and by unmount: a request settling afterwards must not
  // patch the list it no longer belongs to.
  const generation = useRef(0);
  const stopped = useRef(false);
  const updateProfile = useMutation(updateAccountProfileMutation());

  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );

  const patch = (accountId: string, gen: number, next: Partial<BulkProfileRow>) => {
    if (gen !== generation.current) return;
    setRows((prev) => prev.map((row) => (row.accountId === accountId ? { ...row, ...next } : row)));
  };

  const run = async (accountIds: string[], fields: BulkProfileFields) => {
    generation.current += 1;
    const gen = generation.current;
    stopped.current = false;
    setRows(accountIds.map((accountId) => ({ accountId, state: 'queued' as const, error: null })));
    for (const accountId of accountIds) {
      if (stopped.current || gen !== generation.current) break;
      patch(accountId, gen, { state: 'running' });
      try {
        await updateProfile.mutateAsync({ body: { ...fields, account_id: accountId } });
        patch(accountId, gen, { state: 'ok' });
      } catch (error) {
        // Kept, not rethrown: one refused account (occupied username, flood
        // wait, dead session) must not abandon the rest of the batch — the row
        // carries the reason and the operator retries that one.
        patch(accountId, gen, { state: 'error', error });
      }
    }
  };

  // The in-flight account still finishes: its profile is already changing on
  // Telegram, and dropping the answer would only hide which state it landed in.
  const stop = () => {
    stopped.current = true;
  };

  return { rows, run, stop };
}
