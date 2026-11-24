import { useRef, useState } from 'react';

// A bulk flow runs its accounts one by one in the browser, so closing the tab drops the
// rest with no trace. This keeps the queue's PROGRESS in localStorage as each account
// finishes, so the flow can offer to pick up where it stopped.
//
// Only account ids and two outcome words are stored, never a password or any other
// secret: a flow that needs a password to go on asks for it again. Storage that is
// missing, full or refused is not an error: the run goes on, it just cannot be resumed.

const KEY_PREFIX = 'telebuba.bulkRun.';
// An older run is not offered again: the fleet has moved on.
export const RESUME_WINDOW_MS = 24 * 60 * 60 * 1000;

export type RunOutcome = 'done' | 'failed';

export interface RunRecord {
  runId: string;
  startedAt: number;
  ids: string[];
  done: string[];
  failed: string[];
}

// What an unfinished earlier run left behind.
export interface InterruptedRun {
  processed: number;
  total: number;
  // The ids not reached, in their original order.
  remaining: string[];
}

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function isStringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

function parse(raw: string | null): RunRecord | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<RunRecord> | null;
    if (
      value &&
      typeof value.runId === 'string' &&
      typeof value.startedAt === 'number' &&
      isStringList(value.ids) &&
      isStringList(value.done) &&
      isStringList(value.failed)
    ) {
      return value as RunRecord;
    }
  } catch {
    // Not ours or damaged: treated as no run.
  }
  return null;
}

function remainingOf(record: RunRecord): string[] {
  const seen = new Set([...record.done, ...record.failed]);
  return record.ids.filter((id) => !seen.has(id));
}

export function useResumableRun(name: string, { now = Date.now }: { now?: () => number } = {}) {
  const key = KEY_PREFIX + name;
  // The live run, also when storage is unavailable.
  const current = useRef<RunRecord | null>(null);

  const write = (record: RunRecord | null) => {
    try {
      if (record) storage()?.setItem(key, JSON.stringify(record));
      else storage()?.removeItem(key);
    } catch {
      // Quota or a blocked store: the run is just not resumable.
    }
  };

  const [interrupted, setInterrupted] = useState<InterruptedRun | null>(() => {
    let record: RunRecord | null = null;
    try {
      record = parse(storage()?.getItem(key) ?? null);
    } catch {
      record = null;
    }
    if (!record) return null;
    const remaining = remainingOf(record);
    if (remaining.length === 0 || now() - record.startedAt > RESUME_WINDOW_MS) {
      write(null);
      return null;
    }
    return { processed: record.ids.length - remaining.length, total: record.ids.length, remaining };
  });

  const begin = (ids: string[]) => {
    setInterrupted(null);
    if (ids.length === 0) {
      current.current = null;
      write(null);
      return;
    }
    const startedAt = now();
    current.current = {
      runId: `${String(startedAt)}-${Math.random().toString(36).slice(2, 10)}`,
      startedAt,
      ids: [...ids],
      done: [],
      failed: [],
    };
    write(current.current);
  };

  const mark = (accountId: string, outcome: RunOutcome) => {
    const record = current.current;
    if (!record) return;
    const list = outcome === 'done' ? record.done : record.failed;
    if (!list.includes(accountId)) list.push(accountId);
    write(record);
  };

  // The run ended on its own: nothing is left to resume.
  const finish = () => {
    current.current = null;
    write(null);
  };

  // «Сбросить»: forget the earlier run.
  const reset = () => {
    setInterrupted(null);
    write(null);
  };

  // «Продолжить»: the ids still to do that still exist. The caller starts them as a new run.
  const resume = (existing: Iterable<string>): string[] => {
    const alive = new Set(existing);
    const ids = (interrupted?.remaining ?? []).filter((id) => alive.has(id));
    setInterrupted(null);
    return ids;
  };

  return { interrupted, begin, mark, finish, reset, resume };
}
