import { useEffect, useRef, useState } from 'react';

export type FeedbackResult = 'ok' | 'err';

// Schedules auto-clear timers that cannot outlive the component: a card can
// unmount while one is still pending (the account-edit delete calls `onBack()`,
// which unmounts the whole tree while an alive-check timer runs), and the
// callback would then fire against a dead tree.
export function useClearedTimeouts(): (fn: () => void, ms: number) => void {
  const ids = useRef<number[]>([]);
  useEffect(
    () => () => {
      for (const id of ids.current) window.clearTimeout(id);
    },
    [],
  );
  return (fn, ms) => {
    ids.current.push(window.setTimeout(fn, ms));
  };
}

// Tracks a per-key ok/err mark that auto-clears — the shared state machine
// behind every "spinner while pending, checkmark or cross after" mutation.
export function useTransientFeedback(clearMs = 1600) {
  const [feedback, setFeedback] = useState<Record<string, FeedbackResult>>({});
  const later = useClearedTimeouts();

  const mark = (key: string, ok: boolean) => {
    setFeedback((f) => ({ ...f, [key]: ok ? 'ok' : 'err' }));
    later(() => {
      setFeedback((f) => {
        const next = { ...f };
        delete next[key];
        return next;
      });
    }, clearMs);
  };

  return { feedback, mark };
}
