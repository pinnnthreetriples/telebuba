import * as fx from '../e2e/fixtures';

import { board, campaign, scenario, settings } from './campaignFixtures';

import { client } from '@/shared/api/client.gen';

export type PreviewState = 'populated' | 'empty' | 'error' | 'loading';

const routes: [RegExp, unknown][] = [
  [/\/auth\/me$/, fx.me],
  [/\/health$/, fx.health],
  [/\/accounts\/bulk-messages\/(active|latest)$/, null],
  [/\/accounts\/stats$/, fx.accountStats],
  [/\/accounts$/, fx.accounts],
  [/\/proxies$/, fx.proxies],
  [/\/warming\/board$/, fx.warmingBoard],
  [/\/warming\/settings$/, fx.warmingSettings],
  [/\/warming\/dialogues$/, fx.warmingDialogues],
  [/\/warming\/channels$/, { channels: [] }],
  [/\/warming\/warmed$/, { accounts: [] }],
  [/\/neurocomment\/campaigns\/[^/]+\/board$/, fx.neurocommentBoard],
  [
    /\/neurocomment\/campaigns\/[^/]+\/challenges\/counts$/,
    { solved: 0, failed: 0, give_up: 0, pending: 0 },
  ],
  [/\/neurocomment\/campaigns\/[^/]+\/challenges$/, fx.challenges],
  [
    /\/neurocomment\/campaigns\/[^/]+\/comments$/,
    { items: fx.neurocommentBoard.comments, next_cursor: null },
  ],
  [
    /\/neurocomment\/campaigns\/[^/]+\/discovery$/,
    { campaign_id: 'nc-0', progress: { phase: 'idle', running: false }, candidates: [] },
  ],
  [/\/neurocomment\/campaigns\/[^/]+\/channel-bans$/, { items: [] }],
  [/\/neurocomment\/discovery\/accounts$/, { accounts: [] }],
  [/\/neurocomment\/campaigns$/, fx.neurocommentCampaigns],
  [/\/neurocomment\/runtime$/, fx.neurocommentRuntime],
  [/\/neurocomment\/settings$/, fx.neurocommentSettings],
  [/\/neuroshilling\/campaigns\/[^/]+\/board$/, board],
  [/\/neuroshilling\/campaigns\/[^/]+\/scenario$/, scenario],
  [/\/neuroshilling\/campaigns\/[^/]+\/settings$/, settings],
  [/\/neuroshilling\/campaigns\/[^/]+$/, campaign],
  [
    /\/neuroshilling\/campaigns$/,
    {
      campaigns: [
        campaign,
        {
          ...campaign,
          campaign_id: 'ns-1',
          name: 'Обсуждение в чатах',
          mode: 'revive',
          status: 'idle',
        },
      ],
    },
  ],
  [/\/logs$/, fx.logs],
  [/\/logs\/count$/, { matching: fx.logs.items.length }],
];

function empty(value: unknown): unknown {
  if (Array.isArray(value)) return [];
  if (value === null || typeof value !== 'object') return value;
  return Object.fromEntries(Object.entries(value).map(([key, nested]) => [key, empty(nested)]));
}

// Preview-only transport: never falls through to real fetch or backend mutations.
export function previewFetch(state: PreviewState): typeof fetch {
  return async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    const path = new URL(request.url).pathname;
    const authenticated = /\/(?:auth\/me|health)$/.test(path);
    if (request.method !== 'GET') {
      return Response.json(
        { error: { code: 'preview_read_only', message: 'Действие доступно только в приложении' } },
        { status: 409 },
      );
    }
    const route = routes.find(([pattern]) => pattern.test(path));
    if (!route) {
      console.error(`Missing design-system fixture: ${request.method} ${path}`);
      return Response.json(
        { error: { code: 'preview_fixture_missing', message: `Нет тестовых данных: ${path}` } },
        { status: 501 },
      );
    }
    if (state === 'loading' && !authenticated) {
      return new Promise<Response>((_resolve, reject) => {
        const abort = () => reject(new DOMException('Preview request aborted', 'AbortError'));
        if (request.signal.aborted) abort();
        else request.signal.addEventListener('abort', abort, { once: true });
      });
    }
    if (state === 'error' && !authenticated) {
      return Response.json(
        { error: { code: 'preview_error', message: 'Ошибка тестового ответа' } },
        { status: 503 },
      );
    }
    const data = state === 'empty' && !authenticated ? empty(route[1]) : route[1];
    return Response.json(data);
  };
}

class PreviewEventSource extends EventTarget implements EventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSED = 2;
  readonly url: string;
  readonly withCredentials: boolean;
  readyState = 1;
  onopen: EventSource['onopen'] = null;
  onerror: EventSource['onerror'] = null;
  onmessage: EventSource['onmessage'] = null;
  // EventSource narrows custom events to MessageEvent; EventTarget stores the
  // same listeners and dispatches their event objects without transformation.
  override addEventListener: EventSource['addEventListener'] = (
    type: string,
    listener: unknown,
    options?: boolean | AddEventListenerOptions,
  ) => {
    super.addEventListener(type, listener as EventListenerOrEventListenerObject | null, options);
  };
  override removeEventListener: EventSource['removeEventListener'] = (
    type: string,
    listener: unknown,
    options?: boolean | EventListenerOptions,
  ) => {
    super.removeEventListener(type, listener as EventListenerOrEventListenerObject | null, options);
  };
  constructor(url: string | URL, options?: EventSourceInit) {
    super();
    this.url = String(url);
    this.withCredentials = options?.withCredentials ?? false;
    queueMicrotask(() => {
      if (this.readyState !== this.OPEN) return;
      const event = new Event('open');
      this.onopen?.call(this, event);
      this.dispatchEvent(event);
    });
  }
  close() {
    this.readyState = this.CLOSED;
  }
}

export function installPreviewTransport(state: PreviewState): () => void {
  const original = client.getConfig();
  const eventSource = globalThis.EventSource;
  const originalFetch = globalThis.fetch;
  const offlineFetch = previewFetch(state);
  client.setConfig({ fetch: offlineFetch });
  globalThis.fetch = offlineFetch;
  globalThis.EventSource = PreviewEventSource;
  return () => {
    client.setConfig(original);
    globalThis.fetch = originalFetch;
    globalThis.EventSource = eventSource;
  };
}
