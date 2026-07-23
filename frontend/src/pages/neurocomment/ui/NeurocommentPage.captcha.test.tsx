import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import {
  BOARD,
  CAMPAIGN,
  jsonResponse,
  renderWithClient,
  routeApi,
} from './NeurocommentPage.testHelpers';
import { NeurocommentPage } from './NeurocommentPage';

test('toggling the captcha solver persists the campaign override', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  const sw = screen.getByRole('switch', { name: 'Решение капчи' });
  expect(sw).toHaveAttribute('aria-checked', 'true');
  await userEvent.click(sw);
  await waitFor(() => {
    const posted = vi.mocked(fetch).mock.calls.some(([input]) => {
      const request = input as Request;
      return request.url.includes('/campaigns/c1/solver') && request.method === 'POST';
    });
    expect(posted).toBe(true);
  });
});

test('the captcha solver toggle reflects the persisted value after a real round trip', async () => {
  // Unlike routeApi() (a static mock), this simulates a real backend: the POST
  // actually updates the value the next GET /board returns.
  let solverEnabled = true;
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/solver') && request.method === 'POST') {
      return request
        .clone()
        .json()
        .then((body: { enabled: boolean }) => {
          solverEnabled = body.enabled;
          return new Response(null, { status: 204 });
        });
    }
    if (url.pathname.endsWith('/board')) {
      return Promise.resolve(jsonResponse({ ...BOARD, solver_enabled: solverEnabled }));
    }
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: null }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });

  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  const sw = screen.getByRole('switch', { name: 'Решение капчи' });
  expect(sw).toHaveAttribute('aria-checked', 'true');

  await userEvent.click(sw);
  await waitFor(() => {
    expect(sw).toHaveAttribute('aria-checked', 'false');
  });

  await userEvent.click(sw);
  await waitFor(() => {
    expect(sw).toHaveAttribute('aria-checked', 'true');
  });
});

test('Решить retries a challenged pair', async () => {
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
    if (url.pathname.endsWith('/challenges')) {
      return Promise.resolve(
        jsonResponse({
          rows: [
            {
              account_id: 'acc-9',
              channel: '@x',
              raw_text: 'cap',
              outcome: 'failed',
              decided_at: '2026-06-30T12:00:00+00:00',
            },
          ],
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getByText('Пройти')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Пройти'));
  await waitFor(() => {
    const retried = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.endsWith('/neurocomment/retry'));
    expect(retried).toBe(true);
  });
});

test('the captcha queue shows the account phone, not the raw id', async () => {
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
    if (url.pathname.endsWith('/challenges')) {
      return Promise.resolve(
        jsonResponse({
          rows: [
            {
              account_id: 'acc-1',
              channel: '@x',
              raw_text: 'cap',
              outcome: 'failed',
              decided_at: '2026-06-30T12:00:00+00:00',
            },
          ],
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getByText('Пройти')).toBeInTheDocument();
  });
  // Phone from the accounts list, not the raw "acc-1" id.
  expect(screen.getAllByText('+79261112233').length).toBeGreaterThan(0);
  expect(screen.queryByText('acc-1')).not.toBeInTheDocument();
});
