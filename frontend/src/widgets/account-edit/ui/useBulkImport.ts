import { useMutation } from '@tanstack/react-query';
import { useRef, useState } from 'react';

import { importAccountSessionMutation, importAccountTdataMutation } from '@/entities/account';

export type BulkFile = { name: string; state: 'importing' | 'ok' | 'error'; accountIds: string[] };

type Method = 'session' | 'tdata';

// One import request per picked file, at most this many in flight at once.
const MAX_IN_FLIGHT = 2;

// Many `.session` / `tdata.zip` files, each imported by its own request with its
// own outcome and retry. The raw File objects stay in a ref (retry re-sends them);
// only name + verdict are rendered. `mutateAsync`, never `.mutate` in a loop: one
// useMutation observer is a single callback slot.
export function useBulkImport(method: Method, onSettledOne: () => void) {
  const [files, setFiles] = useState<BulkFile[]>([]);
  const raw = useRef<File[]>([]);
  const queue = useRef<number[]>([]);
  const inFlight = useRef(0);
  // Bumped by reset(): a file settling afterwards must not touch the new list.
  const generation = useRef(0);
  const importSession = useMutation(importAccountSessionMutation());
  const importTdata = useMutation(importAccountTdataMutation());

  const patch = (index: number, gen: number, next: Partial<BulkFile>) => {
    if (gen !== generation.current) return;
    setFiles((prev) => prev.map((f, i) => (i === index ? { ...f, ...next } : f)));
  };

  const runOne = async (index: number, file: File, gen: number) => {
    try {
      const accountIds =
        method === 'tdata'
          ? ((await importTdata.mutateAsync({ body: { file } })).accounts?.map(
              (account) => account.account_id,
            ) ?? [])
          : [(await importSession.mutateAsync({ body: { file } })).account_id];
      patch(index, gen, { state: 'ok', accountIds });
    } catch {
      patch(index, gen, { state: 'error' });
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
      const index = queue.current.shift();
      if (index === undefined) return;
      const file = raw.current[index];
      if (!file) continue;
      inFlight.current += 1;
      void runOne(index, file, generation.current);
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
      ...picked.map((file) => ({ name: file.name, state: 'importing' as const, accountIds: [] })),
    ]);
    pump();
  };

  const retry = (index: number) => {
    patch(index, generation.current, { state: 'importing' });
    queue.current.push(index);
    pump();
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
    reset,
    accountIds: files.flatMap((f) => f.accountIds),
    importing: files.some((f) => f.state === 'importing'),
  };
}
