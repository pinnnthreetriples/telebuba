import { useEffect, useRef } from 'react';

import type { LogEntry } from '@/shared/api';

// Same-origin SSE stream of live LogEntry events (the session cookie travels
// with the EventSource automatically).
const EVENTS_URL = '/api/v1/events';

// Trailing debounce window: a burst of frames is buffered and flushed once, so
// every consumer (status pill + each page) reacts to at most one delivery per
// window instead of one per frame.
const FLUSH_MS = 400;

export type SseStatus = 'connecting' | 'open' | 'error';

interface Subscriber {
  onEntry: (entry: LogEntry) => void;
  onStatus?: (status: SseStatus) => void;
}

// One shared EventSource for the whole app, ref-counted across every hook
// instance. Opens on the first subscriber, closes when the last unsubscribes.
let source: EventSource | null = null;
const subscribers = new Set<Subscriber>();
let buffer: LogEntry[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;

function announce(status: SseStatus): void {
  for (const sub of subscribers) sub.onStatus?.(status);
}

function flush(): void {
  flushTimer = null;
  const batch = buffer;
  buffer = [];
  for (const sub of subscribers) {
    for (const entry of batch) sub.onEntry(entry);
  }
}

function openSource(): void {
  source = new EventSource(EVENTS_URL);
  announce('connecting');
  source.onopen = () => {
    announce('open');
  };
  source.onerror = () => {
    announce('error');
  };
  source.onmessage = (event) => {
    let entry: LogEntry;
    try {
      entry = JSON.parse(event.data) as LogEntry;
    } catch {
      return; // Ignore malformed frames; keepalive comments never reach onmessage.
    }
    buffer.push(entry);
    flushTimer ??= setTimeout(flush, FLUSH_MS);
  };
}

function closeSource(): void {
  source?.close();
  source = null;
  if (flushTimer) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
  buffer = [];
}

/**
 * Subscribe to the live log-event stream for the lifetime of the component.
 *
 * All hook instances share a single EventSource (ref-counted). The latest
 * `onEntry`/`onStatus` are always invoked (held in refs) so callers can pass
 * inline closures without resubscribing each render. Delivered entries are
 * debounced (trailing `FLUSH_MS`) so a burst of frames coalesces into one
 * delivery while still surfacing every entry in order.
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
    const sub: Subscriber = {
      onEntry: (entry) => {
        handler.current(entry);
      },
      onStatus: (status) => {
        statusHandler.current?.(status);
      },
    };
    subscribers.add(sub);
    if (source) {
      // A stream already exists — replay the current status to the new
      // subscriber so its pill isn't stuck on the default.
      sub.onStatus?.(source.readyState === source.OPEN ? 'open' : 'connecting');
    } else {
      openSource();
    }
    return () => {
      subscribers.delete(sub);
      if (subscribers.size === 0) closeSource();
    };
  }, []);
}
