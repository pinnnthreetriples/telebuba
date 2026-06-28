import { useEffect, useRef } from 'react';

import type { LogEntry } from '@/shared/api';

// Same-origin SSE stream of live LogEntry events (the session cookie travels
// with the EventSource automatically).
const EVENTS_URL = '/api/v1/events';

/**
 * Subscribe to the live log-event stream for the lifetime of the component.
 *
 * The connection opens once; the latest `onEntry` is always invoked (held in a
 * ref) so callers can pass an inline closure without reconnecting each render.
 */
export function useLogEventStream(onEntry: (entry: LogEntry) => void): void {
  const handler = useRef(onEntry);
  handler.current = onEntry;

  useEffect(() => {
    const source = new EventSource(EVENTS_URL);
    source.onmessage = (event) => {
      try {
        handler.current(JSON.parse(event.data) as LogEntry);
      } catch {
        // Ignore malformed frames; keepalive comments never reach onmessage.
      }
    };
    return () => {
      source.close();
    };
  }, []);
}
