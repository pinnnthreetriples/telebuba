/* eslint-disable react-refresh/only-export-components -- test-only helpers,
   never hot-reloaded; the `satisfies`-typed fixtures trip the rule's
   constant-export detection. */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { vi } from 'vitest';

import '@/shared/i18n';

import type { AccountProfileView, AccountRead } from '@/shared/api';

export function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export const ACCOUNT: AccountRead = {
  account_id: 'acc-1',
  status: 'alive',
  first_name: 'Иван',
  username: 'ivanov',
  phone: '+79991234567',
  created_at: 'now',
  updated_at: 'now',
};

// Typed against the real view schema so the fixture can't drift from the
// contract (int64 ids travel as strings, the main photo carries is_main).
export const VIEW = {
  error: null,
  // Live profile text matching the stored row, so auto-seeding is a no-op in
  // tests that don't exercise it explicitly.
  first_name: 'Иван',
  last_name: null,
  username: 'ivanov',
  bio: null,
  photos: [
    { photo_id: '1', access_hash: '2', file_reference: 'YWJj', thumb_url: null, is_main: true },
  ],
  stories: [
    {
      story_id: 3,
      kind: 'image',
      privacy_preset: 'contacts',
      is_pinned: false,
      views: 128,
      reactions: 24,
      thumb_url: null,
    },
  ],
  music: [
    { file_id: '4', title: 'Track', performer: 'Artist', access_hash: '5', file_reference: 'YWJj' },
  ],
  music_supported: true,
} satisfies AccountProfileView;

export function routeApi() {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(VIEW));
    }
    if (pathname === '/api/v1/accounts/profile') {
      return Promise.resolve(jsonResponse({ ...ACCOUNT, first_name: 'Пётр' }));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
}

export function fired(fragment: string, method = 'POST'): boolean {
  return vi.mocked(fetch).mock.calls.some(([input]) => {
    const request = input as Request;
    return request.url.includes(fragment) && request.method === method;
  });
}

export const TWO_PHOTOS = {
  ...VIEW,
  photos: [
    { photo_id: '111', access_hash: '222', file_reference: 'YWJj', thumb_url: null, is_main: true },
    {
      photo_id: '333',
      access_hash: '444',
      file_reference: 'ZmZm',
      thumb_url: null,
      is_main: false,
    },
  ],
} satisfies AccountProfileView;
