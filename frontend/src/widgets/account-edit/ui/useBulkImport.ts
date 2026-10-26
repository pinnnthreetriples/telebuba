import { useMutation } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import {
  importAccountSessionMutation,
  importAccountTdataMutation,
  recordAccountTwofaFromJsonMutation,
  type DeviceType,
} from '@/entities/account';
import { errorCode, errorMessage } from '@/shared/lib';

// Why a file failed, in the operator's terms rather than the envelope's.
export type ImportFailure =
  | 'duplicate'
  | 'name'
  | 'size'
  | 'broken'
  | 'offline'
  | 'other'
  // The seller's .json: picked without its .session, unreadable, for another phone,
  // or Android while the mobile keys are not configured.
  | 'jsonAlone'
  | 'jsonInvalid'
  | 'jsonMismatch'
  | 'mobileOff';

// `id` is the file's slot in the raw list, stable when other rows are removed.
export type BulkFile = {
  id: number;
  name: string;
  state: 'importing' | 'ok' | 'error';
  accountIds: string[];
  failure?: ImportFailure;
  // A .session imported together with its seller .json (the "+ JSON" mark).
  withJson?: boolean;
  // The outcome of recording the JSON's password, once the proxy step assigned a
  // proxy (the import itself never talks to Telegram): `recorded` makes the 2FA step
  // skip the account; `failed` carries the backend's refusal code. Absent = not tried.
  twofa?: { recorded: true } | { recorded: false; code: string | undefined };
};

const FAILURE_BY_MESSAGE: Record<string, ImportFailure> = {
  session_json_invalid: 'jsonInvalid',
  session_json_phone_mismatch: 'jsonMismatch',
  mobile_api_not_configured: 'mobileOff',
};

// The file name without its extension, the key a .session and its .json pair on.
// A leading `+` is dropped like the backend does for phone-named sessions.
const stemOf = (name: string) =>
  name
    .replace(/\.[^.]+$/, '')
    .replace(/^\+/, '')
    .toLowerCase();
const isJson = (file: File) => file.name.toLowerCase().endsWith('.json');

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
  const byMessage =
    method === 'session' ? FAILURE_BY_MESSAGE[errorMessage(error) ?? ''] : undefined;
  if (byMessage) return byMessage;
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
// `sessionDevice` is the device an uploaded .session belongs to (a tdata archive is
// always a desktop client's); the wizard locks it once the first file is sent.
export function useBulkImport(
  method: Method,
  onSettledOne: () => void,
  sessionDevice: DeviceType = 'desktop',
  // Optional registration date (YYYY-MM-DD) sent with every file of the batch (4.8).
  registeredAt = '',
) {
  const [files, setFiles] = useState<BulkFile[]>([]);
  const raw = useRef<(File | undefined)[]>([]);
  // The seller .json that rides with raw[id], same slot.
  const rawJson = useRef<(File | undefined)[]>([]);
  const queue = useRef<number[]>([]);
  const inFlight = useRef(0);
  // Bumped by reset(): a file settling afterwards must not touch the new list.
  const generation = useRef(0);
  const importSession = useMutation({
    ...importAccountSessionMutation(),
    meta: { inlineError: true },
  });
  const importTdata = useMutation({ ...importAccountTdataMutation(), meta: { inlineError: true } });
  const recordJson = useMutation({
    ...recordAccountTwofaFromJsonMutation(),
    meta: { inlineError: true },
  });

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
      const json = rawJson.current[id];
      const body = {
        file,
        ...(registeredAt ? { registered_at: registeredAt } : {}),
      };
      let accountIds: string[];
      if (method === 'tdata') {
        const result = await importTdata.mutateAsync({ body });
        accountIds = result.accounts?.map((account) => account.account_id) ?? [];
      } else {
        const imported = await importSession.mutateAsync({
          body: { ...body, ...(json ? { json_file: json } : {}), session_device: sessionDevice },
        });
        accountIds = [imported.account_id];
      }
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
    const everything = Array.from(list);
    if (everything.length === 0) return;
    // A seller .json pairs with the .session of the same stem picked in the same
    // batch and rides in its request; a .json with no partner is its own error row.
    const jsons = method === 'session' ? everything.filter(isJson) : [];
    const picked = everything.filter((file) => !jsons.includes(file));
    const partner = new Map(jsons.map((file) => [stemOf(file.name), file]));
    const paired = new Set<File>();
    const start = raw.current.length;
    const rows: BulkFile[] = [];
    picked.forEach((file, i) => {
      const json = partner.get(stemOf(file.name));
      if (json) paired.add(json);
      raw.current[start + i] = file;
      rawJson.current[start + i] = json;
      rows.push({
        id: start + i,
        name: file.name,
        state: 'importing',
        accountIds: [],
        ...(json ? { withJson: true } : {}),
      });
    });
    jsons
      .filter((file) => !paired.has(file))
      .forEach((file, i) => {
        const id = start + picked.length + i;
        raw.current[id] = undefined;
        rawJson.current[id] = undefined;
        rows.push({
          id,
          name: file.name,
          state: 'error',
          accountIds: [],
          failure: 'jsonAlone',
        });
      });
    queue.current.push(...picked.map((_, i) => start + i));
    setFiles((prev) => [...prev, ...rows]);
    pump();
  };

  const retry = (id: number) => {
    // A lone .json has nothing to send again.
    if (!raw.current[id]) return;
    patch(id, generation.current, { state: 'importing', failure: undefined });
    queue.current.push(id);
    pump();
  };

  // Drops a failed row and its credential bytes; nothing exists server-side to undo.
  const remove = (id: number) => {
    raw.current[id] = undefined;
    rawJson.current[id] = undefined;
    setFiles((prev) => prev.filter((f) => f.id !== id));
  };

  // Verify and store every paired JSON's password with Telegram. Run it after the
  // proxy step: an account without a proxy would connect from the operator's own IP,
  // so the backend refuses (`twofa_proxy_missing`) and the row says so. One request
  // at a time, each row keeps its own outcome; never throws.
  const recordJsonPasswords = async () => {
    const gen = generation.current;
    for (const row of files) {
      const json = rawJson.current[row.id];
      const accountId = row.accountIds[0];
      if (!json || !accountId || row.state !== 'ok') continue;
      try {
        await recordJson.mutateAsync({
          path: { account_id: accountId },
          body: { json_file: json },
        });
        patch(row.id, gen, { twofa: { recorded: true } });
      } catch (error) {
        patch(row.id, gen, {
          twofa: { recorded: false, code: errorMessage(error) },
        });
      }
    }
  };

  const reset = () => {
    generation.current += 1;
    raw.current = [];
    rawJson.current = [];
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
    recordJsonPasswords,
    // Accounts whose JSON password was verified and stored: the 2FA step leaves them alone.
    recordedIds: files.filter((f) => f.twofa?.recorded === true).flatMap((f) => f.accountIds),
    // accountId → refusal code for the JSON passwords that could not be recorded.
    recordFailures: Object.fromEntries(
      files.flatMap((f) =>
        f.twofa?.recorded === false && f.accountIds[0]
          ? [[f.accountIds[0], f.twofa.code ?? ''] as [string, string]]
          : [],
      ),
    ),
    importing: files.some((f) => f.state === 'importing'),
  };
}
