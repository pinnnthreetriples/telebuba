import { useEffect, useRef, useState } from 'react';

export type BulkRow = {
  accountId: string;
  state: 'queued' | 'running' | 'ok' | 'error';
  error: unknown;
};

/** One account's share of the batch. `index` is its position, for per-account material. */
export type BulkStep = (accountId: string, index: number) => Promise<unknown>;

// The bulk editor's engine: one account at a time, in list order, whatever the
// tab is applying — profile text, a photo, a story, a track.
//
// STRICTLY sequential, like useBulkTwofa and unlike useBulkImport's two-at-a-time
// pump. Not because the sessions share anything — they don't — but because these
// are flood-sensitive actions whose FloodWait writes the sticky `flood_wait`
// status, and a fleet-wide fan-out turns one rate-limited account into seventeen.
// ponytail: one at a time; raise the width here if a large fleet ever makes the
// wall-clock the complaint.
//
// The step is passed per run rather than baked in: the tabs differ only in what
// one account's turn does, and every one of them needs the same rows, the same
// stop and the same generation guard.
export function useBulkRun() {
  const [rows, setRows] = useState<BulkRow[]>([]);
  // Bumped by a new run and by unmount: a request settling afterwards must not
  // patch the list it no longer belongs to.
  const generation = useRef(0);
  const stopped = useRef(false);

  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );

  const patch = (accountId: string, gen: number, next: Partial<BulkRow>) => {
    if (gen !== generation.current) return;
    setRows((prev) => prev.map((row) => (row.accountId === accountId ? { ...row, ...next } : row)));
  };

  const run = async (accountIds: string[], step: BulkStep) => {
    generation.current += 1;
    const gen = generation.current;
    stopped.current = false;
    setRows(accountIds.map((accountId) => ({ accountId, state: 'queued' as const, error: null })));
    for (const [index, accountId] of accountIds.entries()) {
      if (stopped.current || gen !== generation.current) break;
      patch(accountId, gen, { state: 'running' });
      try {
        await step(accountId, index);
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
