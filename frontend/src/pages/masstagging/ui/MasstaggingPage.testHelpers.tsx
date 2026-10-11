import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { vi } from 'vitest';

import '@/shared/i18n';

import type { MasstaggingBoard, MasstaggingCampaign, MasstaggingSettingsRead } from '@/shared/api';

import { board, CAMPAIGN, SETTINGS_READ } from '../model/fixtures.test-helpers';

import { MasstaggingPage } from './MasstaggingPage';

export const FLEET = [
  {
    account_id: 'a1',
    status: 'alive',
    first_name: 'Арал',
    phone: '1',
    username: 'aral',
    premium: true,
  },
  {
    account_id: 'a2',
    status: 'alive',
    first_name: 'Юлия',
    phone: '2',
    username: null,
    premium: false,
  },
  {
    account_id: 'a3',
    status: 'alive',
    first_name: 'Софья',
    phone: '3',
    username: 'sofya',
    spam_status: 'limited',
  },
];

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export type Api = {
  campaigns: MasstaggingCampaign[];
  board: MasstaggingBoard;
  settings: MasstaggingSettingsRead;
  // A refusal the next write to a path ending like this answers with.
  refuse: { path: string; status: number; code: string } | null;
};

export function routeApi(over: Partial<Api> = {}): Api {
  const api: Api = {
    campaigns: [CAMPAIGN],
    board: board(),
    settings: SETTINGS_READ,
    refuse: null,
    ...over,
  };
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const path = new URL(request.url).pathname;
    if (api.refuse !== null && path.endsWith(api.refuse.path) && request.method !== 'GET') {
      return jsonResponse(
        { error: { code: 'conflict', message: api.refuse.code } },
        api.refuse.status,
      );
    }
    if (path === '/api/v1/accounts') return jsonResponse({ items: FLEET, next_cursor: null });
    if (path === '/api/v1/logs/count') return jsonResponse({ matching: 4 });
    if (path === '/api/v1/logs') return jsonResponse({ items: [] });
    if (path === '/api/v1/user-parser/bases') {
      return jsonResponse({
        items: [
          {
            run_id: 'b1',
            name: 'Москва',
            mode: 'members',
            status: 'done',
            created_at: '2026-10-01',
            sources: [],
            kept: 2,
          },
        ],
      });
    }
    if (path === '/api/v1/masstagging/campaigns') {
      if (request.method === 'POST') {
        const created = { ...CAMPAIGN, campaign_id: 'c9', name: 'Новая', status: 'draft' as const };
        api.campaigns = [...api.campaigns, created];
        return jsonResponse(created);
      }
      return jsonResponse({ items: api.campaigns });
    }
    if (path.endsWith('/targets/import')) {
      return jsonResponse({ targets: ['@filed', '@other'], skipped: 1, name: '' });
    }
    if (path.endsWith('/targets/base')) {
      return jsonResponse({ targets: ['@base1', '@base2'], skipped: 0, name: 'Москва' });
    }
    if (path.endsWith('/media')) {
      return jsonResponse({ media_id: `${'b'.repeat(64)}.png`, name: 'cover.png' });
    }
    if (path.endsWith('/settings') || path.endsWith('/pace')) return jsonResponse(api.settings);
    if (path.endsWith('/board') || path.endsWith('/start') || path.endsWith('/stop')) {
      return jsonResponse(api.board);
    }
    if (path.endsWith('/targets/action')) return jsonResponse(api.board);
    if (request.method === 'DELETE') return new Response(null, { status: 204 });
    return jsonResponse({});
  });
  return api;
}

export function renderPage(ui: ReactElement = <MasstaggingPage />) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

export function callsTo(pathname: string, method: string): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => new URL(request.url).pathname === pathname && request.method === method);
}
