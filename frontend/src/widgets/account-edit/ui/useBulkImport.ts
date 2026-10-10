import { useMutation } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { importAccountSessionMutation, importAccountTdataMutation } from '@/entities/account';
import { errorCode } from '@/shared/lib';

// Why a file failed, in the operator's terms rather than the envelope's.
export type ImportFailure = 'duplicate' | 'name' | 'size' | 'broken' | 'offline' | 'other';

// `id` is the file's slot in the raw list, stable when other rows are removed.
export type BulkFile = {
  id: number;
  name: string;
  state: 'importing' | 'ok' | 'error';
  accountIds: string[];
  failure?: ImportFailure;
};

const FAILURE_BY_CODE: Record<string, ImportFailure> = {
  conflict: 'duplicate',
  validation_error: 'name',
  payload_too_large: 'size',
  bad_request: 'broken',
};

type Method = 'session' | 'tdata';

// No envelope means no answer reached us; an unlisted code gets the neutral reason.
// A tdata name is never validated, so its `validation_error` is an empty archive.
function failureFor(method: Method, error: unknown): ImportFailure {
  const code = errorCode(error);
  if (code === undefined) return 'offline';
  if (method === 'tdata' && code === 'validation_error') return 'broken';
  return FAILURE_BY_CODE[code] ?? 'other';
}

// One import request per picked file, at most this many in flight at once.
const MAX_IN_FLIGHT = 2;

// Many `.session` / `tdata.zip` files, each imported by its own request with its
// own outcome and retry. The raw File objects stay in a ref (retry re-sends them);
// only name + verdict are rendered. Each row reports its own failure, so the
// mutations opt out of the global error toast. `mutateAsync`, never `.mutate` in a loop: one
// useMutation observer is a single callback slot.
export function useBulkImport(method: Method, onSettledOne: () => void) {
  const [files, setFiles] = useState<BulkFile[]>([]);
  const raw = useRef<(File | undefined)[]>([]);
  const queue = useRef<number[]>([]);
  const inFlight = useRef(0);
  // Bumped by reset(): a file settling afterwards must not touch the new list.
  const generation = useRef(0);
  const importSession = useMutation({
    ...importAccountSessionMutation(),
    meta: { inlineError: true },
  });
  const importTdata = useMutation({ ...importAccountTdataMutation(), meta: { inlineError: true } });

  // Cancel/× mid-batch: files already in flight finish (the account exists
  // server-side either way), but the queued ones must not keep POSTing
  // credentials behind a closed wizard.
  useEffect(
    () => () => {
      generation.current += 1;
      queue.current = [];
    },
    [],
  );

  const patch = (id: number, gen: number, next: Partial<BulkFile>) => {
    if (gen !== generation.current) return;
    setFiles((prev) => prev.map((f) => (f.id === id ? { ...f, ...next } : f)));
  };

  const runOne = async (id: number, file: File, gen: number) => {
    try {
      const accountIds =
        method === 'tdata'
          ? ((await importTdata.mutateAsync({ body: { file } })).accounts?.map(
              (account) => account.account_id,
            ) ?? [])
          : [(await importSession.mutateAsync({ body: { file } })).account_id];
      patch(id, gen, { state: 'ok', accountIds });
    } catch (error) {
      patch(id, gen, {
        state: 'error',
        failure: failureFor(method, error),
      });
    } finally {
      // The account exists server-side even when this wizard has moved on, so the
      // accounts table refetches regardless of the generation.
      onSettledOne();
      if (gen === generation.current) {
        inFlight.current -= 1;
        pump();
      }
    }
  };

  const pump = () => {
    while (inFlight.current < MAX_IN_FLIGHT) {
      const id = queue.current.shift();
      if (id === undefined) return;
      const file = raw.current[id];
      if (!file) continue;
      inFlight.current += 1;
      void runOne(id, file, generation.current);
    }
  };

  const add = (list: FileList | File[]) => {
    const picked = Array.from(list);
    if (picked.length === 0) return;
    const start = raw.current.length;
    raw.current.push(...picked);
    queue.current.push(...picked.map((_, i) => start + i));
    setFiles((prev) => [
      ...prev,
      ...picked.map((file, i) => ({
        id: start + i,
        name: file.name,
        state: 'importing' as const,
        accountIds: [],
      })),
    ]);
    pump();
  };

  const retry = (id: number) => {
    patch(id, generation.current, { state: 'importing', failure: undefined });
    queue.current.push(id);
    pump();
  };

  // Drops a failed row and its credential bytes; nothing exists server-side to undo.
  const remove = (id: number) => {
    raw.current[id] = undefined;
    setFiles((prev) => prev.filter((f) => f.id !== id));
  };

  const reset = () => {
    generation.current += 1;
    raw.current = [];
    queue.current = [];
    inFlight.current = 0;
    setFiles([]);
  };

  return {
    files,
    add,
    retry,
    remove,
    reset,
    accountIds: files.flatMap((f) => f.accountIds),
    importing: files.some((f) => f.state === 'importing'),
  };
}
