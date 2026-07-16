import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import {
  BOARD,
  CAMPAIGN,
  jsonResponse,
  lastEventSource,
  renderWithClient,
  routeApi,
} from './NeurocommentPage.testHelpers';
import { NeurocommentPage } from './NeurocommentPage';

test('refetches runtime/board on a live SSE event', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  const boardCalls = () =>
    vi.mocked(fetch).mock.calls.filter(([input]) => (input as Request).url.endsWith('/board'))
      .length;
  const before = boardCalls();
  act(() => {
    lastEventSource()?.emit({ id: 1, event: 'neurocomment_comment_posted' });
  });
  await waitFor(() => {
    expect(boardCalls()).toBeGreaterThan(before);
  });
});

test('the pipeline stats include the errors odometer', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  expect(screen.getByText('ошибок')).toBeInTheDocument();
});

test('the neuro log localizes a known event code and falls back for an unknown one', async () => {
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
    if (url.pathname === '/api/v1/logs') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              id: 1,
              created_at: 'now',
              level: 'INFO',
              status: 'success',
              event: 'neurocomment_posted',
            },
            {
              id: 2,
              created_at: 'now',
              level: 'INFO',
              status: 'success',
              event: 'some_unmapped_code',
            },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getByText('Комментарий опубликован')).toBeInTheDocument();
  });
  // Unmapped code renders verbatim.
  expect(screen.getByText('some_unmapped_code')).toBeInTheDocument();
});

test('the clear-log trash confirms, then DELETEs only the neurocomment logs', async () => {
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
    if (url.pathname === '/api/v1/logs' && request.method === 'GET') {
      return Promise.resolve(
        jsonResponse({
          items: [{ id: 1, created_at: 'now', level: 'INFO', status: 'success', event: 'x' }],
          next_cursor: null,
        }),
      );
    }
    if (url.pathname === '/api/v1/logs' && request.method === 'DELETE') {
      return Promise.resolve(jsonResponse({ deleted: 1 }));
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getByLabelText('Очистить лог')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByLabelText('Очистить лог'));
  const confirm = await screen.findByText('Очистить');
  const wasDeleted = () =>
    vi.mocked(fetch).mock.calls.some(([input]) => {
      const request = input as Request;
      const url = new URL(request.url);
      return (
        request.method === 'DELETE' &&
        url.pathname === '/api/v1/logs' &&
        url.searchParams.get('event_prefix') === 'neurocomment'
      );
    });
  expect(wasDeleted()).toBe(false); // not until confirmed
  await userEvent.click(confirm);
  await waitFor(() => {
    expect(wasDeleted()).toBe(true);
  });
});

test('the SSE callback invalidates only this page keys, not the whole cache', async () => {
  routeApi();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(queryClient, 'invalidateQueries');
  render(<QueryClientProvider client={queryClient}>{<NeurocommentPage />}</QueryClientProvider>);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  spy.mockClear();
  act(() => {
    lastEventSource()?.emit({ id: 1, event: 'neurocomment_posted', status: 'success' });
  });
  await waitFor(() => {
    expect(spy).toHaveBeenCalled();
  });
  // Every SSE-driven invalidation is scoped by a predicate (not a bare call).
  expect(
    spy.mock.calls.every(([arg]) => typeof arg === 'object' && arg !== null && 'predicate' in arg),
  ).toBe(true);
});
