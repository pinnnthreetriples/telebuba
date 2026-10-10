import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { vi } from 'vitest';

import type { AccountRead } from '@/shared/api';

// Shared across AccountsPage.test.tsx and AccountsPage.search.test.tsx: the page
// suite outgrew the 700-line source cap, so the search behaviours moved to their
// own file and these fixtures moved here rather than being copied into both.
export function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

export function account(id: string): AccountRead {
  return { account_id: id, status: 'alive', created_at: 'now', updated_at: 'now' };
}

// How many GET /accounts calls the mocked fetch has seen — the debounce and the
// refetch tests both count them.
export function listGets(): number {
  return vi.mocked(fetch).mock.calls.filter(([input]) => {
    const request = input as Request;
    return new URL(request.url).pathname === '/api/v1/accounts' && request.method === 'GET';
  }).length;
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

// Route the mocked fetch by path/method so list + stats + actions + pagination resolve.
export function routeApi(options: {
  page1: unknown;
  page2?: unknown;
  listStatus?: number;
  checkStatus?: number;
  checked?: AccountRead;
  stats?: unknown;
  folders?: unknown;
  filterOptions?: unknown;
}) {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/accounts/stats' && request.method === 'GET') {
      return Promise.resolve(
        jsonResponse(options.stats ?? { total: 0, active: 0, idle: 0, needs_code: 0, problem: 0 }),
      );
    }
    if (url.pathname === '/api/v1/account-folders' && request.method === 'GET') {
      return Promise.resolve(
        jsonResponse(options.folders ?? { items: [], total_count: 0, unfiled_count: 0 }),
      );
    }
    if (url.pathname === '/api/v1/accounts/filter-options' && request.method === 'GET') {
      return Promise.resolve(
        jsonResponse(
          options.filterOptions ?? { phone_codes: [], proxy_countries: [], no_proxy_count: 0 },
        ),
      );
    }
    if (url.pathname === '/api/v1/accounts' && request.method === 'GET') {
      if (options.listStatus && options.listStatus >= 400) {
        return Promise.resolve(jsonResponse({ detail: 'boom' }, options.listStatus));
      }
      const body = url.searchParams.get('cursor')
        ? (options.page2 ?? options.page1)
        : options.page1;
      return Promise.resolve(jsonResponse(body));
    }
    if (url.pathname === '/api/v1/proxies' && request.method === 'GET') {
      return Promise.resolve(
        jsonResponse({
          proxies: [
            {
              id: 'p1',
              proxy_type: 'socks5',
              host: 'nl',
              port: 1080,
              has_password: false,
              status: 'unknown',
              used: 0,
              capacity: 3,
              free: 3,
              created_at: 'now',
              updated_at: 'now',
            },
          ],
        }),
      );
    }
    if (url.pathname === '/api/v1/accounts/check') {
      if (options.checkStatus) {
        return Promise.resolve(jsonResponse({ detail: 'boom' }, options.checkStatus));
      }
      if (options.checked) return Promise.resolve(jsonResponse(options.checked));
    }
    return Promise.resolve(jsonResponse(account('acc-1')));
  });
}
