import { useState } from 'react';

export type FeedbackResult = 'ok' | 'err';

// Tracks a per-key ok/err mark that auto-clears — the shared state machine
// behind every "spinner while pending, checkmark or cross after" mutation.
export function useTransientFeedback(clearMs = 1600) {
  const [feedback, setFeedback] = useState<Record<string, FeedbackResult>>({});

  const mark = (key: string, ok: boolean) => {
    setFeedback((f) => ({ ...f, [key]: ok ? 'ok' : 'err' }));
    window.setTimeout(() => {
      setFeedback((f) => {
        const next = { ...f };
        delete next[key];
        return next;
      });
    }, clearMs);
  };

  return { feedback, mark };
}
