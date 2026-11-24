// A bulk import sends one request per file, and several accounts behind one proxy that all
// connect back to back read as a burst from that proxy. So consecutive imports are spaced
// out by a pause drawn afresh each time, and (when a file's proxy is known before its
// request) files that share a proxy are not put next to each other.

export const IMPORT_PAUSE_MIN_MS = 1500;
export const IMPORT_PAUSE_MAX_MS = 4000;

// The pause before the next import, in the default range. `random` is injectable.
export function jitteredPause(
  random: () => number = Math.random,
  min = IMPORT_PAUSE_MIN_MS,
  max = IMPORT_PAUSE_MAX_MS,
): number {
  return Math.round(min + random() * (max - min));
}

// The live setting. A tests' setup file zeroes it so a batch of files is not slowed down;
// a test of the pacing itself passes its own `pause` to the hook instead.
export const importPacing = { pauseMs: (): number => jitteredPause() };

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

// Reorders `ids` so two neighbours do not share a proxy when another order exists, and
// changes nothing otherwise: each step takes the first id whose proxy differs from the one
// before it (`previous` is the proxy of the import that went last), else simply the first.
// An id whose proxy is unknown never conflicts.
export function spreadByProxy(
  ids: readonly number[],
  proxyOf: (id: number) => string | null | undefined,
  previous?: string | null,
): number[] {
  const pending = [...ids];
  const result: number[] = [];
  let last = previous ?? null;
  while (pending.length > 0) {
    const index = pending.findIndex((id) => {
      const proxy = proxyOf(id);
      return last === null || proxy == null || proxy !== last;
    });
    const [id] = pending.splice(index === -1 ? 0 : index, 1);
    if (id === undefined) break;
    result.push(id);
    last = proxyOf(id) ?? null;
  }
  return result;
}
