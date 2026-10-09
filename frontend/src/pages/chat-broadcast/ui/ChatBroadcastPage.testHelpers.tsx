import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { vi } from 'vitest';

import '@/shared/i18n';

import type {
  ChatBroadcastBoard,
  ChatBroadcastCampaign,
  ChatBroadcastSettingsRead,
  ChatCollection,
} from '@/shared/api';

import { board, CAMPAIGN, SETTINGS_READ } from '../model/fixtures.test-helpers';

import { ChatBroadcastPage } from './ChatBroadcastPage';

export const FLEET = [
  { account_id: 'a1', status: 'alive', first_name: 'Арал', phone: '1', username: 'aral' },
  { account_id: 'a2', status: 'alive', first_name: 'Юлия', phone: '2', username: null },
  { account_id: 'a3', status: 'alive', first_name: 'Марина', phone: '3', username: 'marina' },
];

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const LOG_ROW = {
  id: 7,
  created_at: '2026-10-06T10:00:00+00:00',
  level: 'INFO',
  status: 'success',
  account_id: null,
  event: 'chat_broadcast_run_started',
  extra: {},
};

export type Api = {
  campaigns: ChatBroadcastCampaign[];
  board: ChatBroadcastBoard;
  settings: ChatBroadcastSettingsRead;
  // The saved chat categories; POST, PUT and DELETE on them change this list.
  collections: ChatCollection[];
  // A refusal the next PUT, POST /start or POST /targets/action answers with.
  refuse: { path: string; status: number; code: string } | null;
};

export function routeApi(over: Partial<Api> = {}): Api {
  const api: Api = {
    campaigns: [CAMPAIGN],
    board: board(),
    settings: SETTINGS_READ,
    collections: [],
    refuse: null,
    ...over,
  };
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const url = new URL(request.url);
    const path = url.pathname;
    if (api.refuse !== null && path.endsWith(api.refuse.path) && request.method !== 'GET') {
      return jsonResponse(
        { error: { code: 'conflict', message: api.refuse.code } },
        api.refuse.status,
      );
    }
    if (path === '/api/v1/accounts') return jsonResponse({ items: FLEET, next_cursor: null });
    if (path === '/api/v1/logs/count') return jsonResponse({ matching: 12 });
    if (path === '/api/v1/logs' && request.method === 'GET')
      return jsonResponse({ items: [LOG_ROW] });
    if (path === '/api/v1/chat-broadcast/campaigns') {
      if (request.method === 'POST') {
        const created = { ...CAMPAIGN, campaign_id: 'c9', name: 'Новая', status: 'draft' as const };
        api.campaigns = [...api.campaigns, created];
        return jsonResponse(created);
      }
      return jsonResponse({ items: api.campaigns });
    }
    if (path.startsWith('/api/v1/chat-broadcast/collections')) {
      const id = path.split('/')[5];
      if (request.method === 'GET') return jsonResponse({ items: api.collections });
      if (request.method === 'DELETE') {
        api.collections = api.collections.filter((item) => item.collection_id !== id);
        return new Response(null, { status: 204 });
      }
      const body = (await request.clone().json()) as { name: string; targets: string[] };
      const saved = {
        collection_id: id ?? 'k9',
        ...body,
        updated_at: '2026-10-07T10:00:00+00:00',
      };
      api.collections = id
        ? api.collections.map((item) => (item.collection_id === id ? saved : item))
        : [...api.collections, saved];
      return jsonResponse(saved);
    }
    if (path === '/api/v1/chat-broadcast/own-chats') {
      return jsonResponse({
        groups: [
          { peer_id: '1', title: 'Общая', username: null, account_ids: ['a1', 'a2'] },
          { peer_id: '2', title: 'Своя', username: 'own', account_ids: ['a1'] },
        ],
        channels_skipped: 3,
        admin_only_skipped: 1,
        unavailable_account_ids: [],
      });
    }
    if (path === '/api/v1/chat-broadcast/targets/resolve') {
      const body = (await request.clone().json()) as { targets: string[] };
      return jsonResponse({
        items: body.targets.map((raw) =>
          raw.startsWith('!')
            ? { raw, error: 'invalid_target' }
            : raw.includes('addlist')
              ? { raw, key: 'addlist:x', kind: 'folder', folder_count: 9 }
              : { raw, key: raw, kind: 'public' },
        ),
      });
    }
    if (path === '/api/v1/chat-broadcast/media') {
      return jsonResponse({ media_id: `${'a'.repeat(64)}.png`, name: 'bot.png' });
    }
    if (path.endsWith('/settings')) {
      if (request.method === 'PUT') return jsonResponse(api.settings);
      return jsonResponse(api.settings);
    }
    if (path.endsWith('/board') || path.endsWith('/start') || path.endsWith('/stop')) {
      return jsonResponse(api.board);
    }
    if (path.endsWith('/targets/action')) return jsonResponse(api.board);
    if (request.method === 'DELETE') return new Response(null, { status: 204 });
    return jsonResponse({});
  });
  return api;
}

export function renderPage(ui: ReactElement = <ChatBroadcastPage />) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

export function callsTo(pathname: string, method: string): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => new URL(request.url).pathname === pathname && request.method === method);
}

export function emitLogFrame(): void {
  const stream = (
    globalThis.EventSource as unknown as { last(): { emit(data: unknown): void } }
  ).last();
  stream.emit({ id: 1, ts: 'now', level: 'info', event: 'chat_broadcast_message_sent' });
}
