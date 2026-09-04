import { useMutation } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { setAccountTwofaMutation } from '@/entities/account';
import type { AccountTwoFactorCreated, AccountTwoFactorUpdateRequest } from '@/shared/api';

export type BulkTwofaRow = {
  accountId: string;
  state: 'queued' | 'running' | 'ok' | 'error';
  created: AccountTwoFactorCreated | null;
  error: unknown;
};

// The add-wizard's cloud-password batch: one account at a time, in list order.
//
// STRICTLY sequential, unlike useBulkImport's two-at-a-time pump, and that is the
// point rather than an omission. Setting a password is an SRP computation the
// backend hands to a bounded pool of two worker threads; `twofa_password_compute_timeout`
// is the refusal that pool's exhaustion looks like from here. Fanning out cannot
// go faster than the pool and can only buy that refusal, so there is no queue.
//
// `mutateAsync` in a plain `for`, never `.mutate` per row: one useMutation
// observer is a single callback slot (and the ESLint rule that says so).
export function useBulkTwofa() {
  const [rows, setRows] = useState<BulkTwofaRow[]>([]);
  // Bumped by a new run and by unmount: a request settling afterwards must not
  // patch the list it no longer belongs to.
  const generation = useRef(0);
  const stopped = useRef(false);
  // `gcTime: 0` beside the `reset()` below, for the reason TwoFactorForm gives:
  // reset() detaches the observer but only SCHEDULES collection, so at the
  // 5-minute default the returned plaintext — and the typed password in
  // `variables` — would sit in the mutation cache long after this step let go.
  const setTwofa = useMutation({ ...setAccountTwofaMutation(), gcTime: 0 });

  // Bumping the generation is the whole teardown: the loop below breaks on it
  // and `patch` refuses on it, so a second `stopped.current = true` here would
  // be a flag nothing reads.
  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );

  const patch = (accountId: string, gen: number, next: Partial<BulkTwofaRow>) => {
    if (gen !== generation.current) return;
    setRows((prev) => prev.map((row) => (row.accountId === accountId ? { ...row, ...next } : row)));
  };

  const run = async (accountIds: string[], body: AccountTwoFactorUpdateRequest) => {
    generation.current += 1;
    const gen = generation.current;
    stopped.current = false;
    setRows(
      accountIds.map((accountId) => ({
        accountId,
        state: 'queued' as const,
        created: null,
        error: null,
      })),
    );
    for (const accountId of accountIds) {
      if (stopped.current || gen !== generation.current) break;
      patch(accountId, gen, { state: 'running' });
      try {
        const created = await setTwofa.mutateAsync({
          path: { account_id: accountId },
          body,
        });
        // Read, then drop: the response and the variables both carry the
        // plaintext, and the rows below are the only copy this step keeps.
        setTwofa.reset();
        patch(accountId, gen, { state: 'ok', created });
      } catch (error) {
        setTwofa.reset();
        patch(accountId, gen, { state: 'error', error });
      }
    }
  };

  // The in-flight account still finishes — its password exists on Telegram
  // either way, and dropping the response would lose the only copy of it.
  const stop = () => {
    stopped.current = true;
  };

  return { rows, run, stop };
}
