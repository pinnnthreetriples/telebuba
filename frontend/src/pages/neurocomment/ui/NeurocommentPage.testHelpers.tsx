import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { vi } from 'vitest';

export function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

export function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

export const CAMPAIGN = {
  campaign_id: 'c1',
  name: 'Promo',
  prompt: 'mention the product',
  status: 'active',
  created_at: 'now',
  updated_at: 'now',
  channel_count: 3,
  account_count: 5,
};

export const BOARD = {
  campaign_id: 'c1',
  campaign_name: 'Promo',
  status: 'active',
  solver_enabled: true,
  channels: [{ channel: '@news', status: 'ready', ready_accounts: 1, total_accounts: 1 }],
  accounts: [
    {
      account_id: 'acc-1',
      label: '+79261112233',
      health: 'ok',
      trust_score: 80,
      trust_band: 'good',
      comments_last_hour: 0,
      max_comments_per_hour: 10,
      comments_today: 2,
      last_comment_at: 'now',
      readiness: [{ channel: '@news', ready: true, joined: true, captcha_passed: true }],
    },
  ],
};

export function routeApi() {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: null }),
      );
    }
    if (url.pathname === '/api/v1/accounts') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              account_id: 'acc-1',
              label: '+79261112233',
              status: 'alive',
              created_at: 'n',
              updated_at: 'n',
            },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });
}

export function lastEventSource(): { emit(data: unknown): void } | undefined {
  return (
    globalThis.EventSource as unknown as { last(): { emit(d: unknown): void } | undefined }
  ).last();
}

// Variant of routeApi where the runtime already has a listener and is running,
// so the page renders the listening surface + its pause/edit/remove actions.
export function routeApiRunning() {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: true, active_channels: 1, listener_account_id: 'acc-1' }),
      );
    }
    if (url.pathname === '/api/v1/accounts') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              account_id: 'acc-1',
              label: '+79261112233',
              status: 'alive',
              created_at: 'n',
              updated_at: 'n',
            },
            {
              account_id: 'acc-2',
              label: '+79261119999',
              status: 'alive',
              created_at: 'n',
              updated_at: 'n',
            },
          ],
          next_cursor: null,
        }),
      );
    }
    if (url.pathname === '/api/v1/warming/warmed') {
      // acc-2 is graduated + handed off to NC and unlinked → the idle account.
      return Promise.resolve(
        jsonResponse({
          accounts: [{ account_id: 'acc-2', label: '+79261119999', nc_handed_off: true }],
        }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });
}
