import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { vi } from 'vitest';

import type { AccountRead } from '@/shared/api';

// Shared by the spam-card specs (check control, appeal block, the card itself): a client that
// does not retry, a request recorder and an account the card can render.

export function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

// The error envelope the API answers a refusal with; `message` is the stable code.
export function refusal(status: number, code: string, message: string): Response {
  return json({ error: { code, message } }, status);
}

export interface Call {
  method: string;
  path: string;
  body: unknown;
}

type Handler = (call: Call) => Response | Promise<Response>;

// Routes the mocked fetch by "METHOD /path" and returns every call it saw, body included. A
// route nobody declared answers an empty object, which every card read treats as "nothing".
export function routeApi(routes: Record<string, Handler>): Call[] {
  const calls: Call[] = [];
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const text = await request.clone().text();
    const call: Call = {
      method: request.method,
      path: new URL(request.url).pathname,
      body: text ? (JSON.parse(text) as unknown) : null,
    };
    calls.push(call);
    const handler = routes[`${call.method} ${call.path}`];
    return handler ? handler(call) : json({});
  });
  return calls;
}

export function callsTo(calls: Call[], method: string, path: string): Call[] {
  return calls.filter((call) => call.method === method && call.path === path);
}

export const HOUR_MS = 3_600_000;

export function ago(hours: number): string {
  return new Date(Date.now() - hours * HOUR_MS).toISOString();
}

export function ahead(hours: number): string {
  return new Date(Date.now() + hours * HOUR_MS).toISOString();
}

export const LIMITED: AccountRead = {
  account_id: 'acc-1',
  status: 'alive',
  proxy_id: 'p1',
  spam_status: 'limited',
  spam_verdict: 'limited',
  spam_confidence: 'high',
  spam_source: 'spambot',
  created_at: 'now',
  updated_at: 'now',
};

export const CLEAN: AccountRead = {
  ...LIMITED,
  spam_status: 'clean',
  spam_verdict: 'clean',
};
