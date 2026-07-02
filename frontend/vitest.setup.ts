import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';

import { client } from '@/shared/api/client.gen';

// Node's fetch (undici) rejects relative URLs that a browser would resolve, and
// the generated client captures globalThis.fetch at import. So for tests give
// the client an absolute base and a controllable fetch; tests drive responses
// via vi.mocked(fetch).
vi.stubGlobal('fetch', vi.fn());
client.setConfig({ baseUrl: 'http://localhost', fetch: globalThis.fetch });

// happy-dom has no EventSource; provide a controllable mock so SSE hooks render
// in tests and specs can drive messages via MockEventSource.last()?.emit(...).
class MockEventSource {
  static instances: MockEventSource[] = [];
  static last(): MockEventSource | undefined {
    return MockEventSource.instances.at(-1);
  }
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSED = 2;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readyState = 0;
  constructor(public url: string) {
    MockEventSource.instances.push(this);
  }
  close(): void {
    this.readyState = 2;
  }
  emit(data: unknown): void {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) }));
  }
  emitOpen(): void {
    this.readyState = 1;
    this.onopen?.();
  }
  emitError(): void {
    this.onerror?.();
  }
}
vi.stubGlobal('EventSource', MockEventSource);

afterEach(() => {
  vi.mocked(fetch).mockReset();
  MockEventSource.instances = [];
});
