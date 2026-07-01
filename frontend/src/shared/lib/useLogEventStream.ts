import { useEffect, useRef } from 'react';

import type { LogEntry } from '@/shared/api';

// Same-origin SSE stream of live LogEntry events (the session cookie travels
// with the EventSource automatically).
const EVENTS_URL = '/api/v1/events';

export type SseStatus = 'connecting' | 'open' | 'error';

/**
 * Subscribe to the live log-event stream for the lifetime of the component.
 *
 * The connection opens once; the latest `onEntry`/`onStatus` are always
 * invoked (held in refs) so callers can pass inline closures without
 * reconnecting each render.
 */
export function useLogEventStream(
  onEntry: (entry: LogEntry) => void,
  onStatus?: (status: SseStatus) => void,
): void {
  const handler = useRef(onEntry);
  handler.current = onEntry;
  const statusHandler = useRef(onStatus);
  statusHandler.current = onStatus;

  useEffect(() => {
    const source = new EventSource(EVENTS_URL);
    statusHandler.current?.('connecting');
    source.onopen = () => {
      statusHandler.current?.('open');
    };
    source.onerror = () => {
      statusHandler.current?.('error');
    };
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
