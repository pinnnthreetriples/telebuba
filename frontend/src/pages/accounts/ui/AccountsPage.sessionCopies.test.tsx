import { screen, waitFor } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { AccountsPage } from './AccountsPage';
import { account, jsonResponse, renderWithClient } from './AccountsPage.test-helpers';

// The Accounts page warns, with no dismiss, while the session-key copies are off. The guard
// status is the same query the Settings card reads.
function routeApi(guard: () => Response) {
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/session-guard') return Promise.resolve(guard());
    if (pathname === '/api/v1/accounts/stats') {
      return Promise.resolve(
        jsonResponse({ total: 1, active: 1, idle: 0, needs_code: 0, problem: 0 }),
      );
    }
    if (pathname === '/api/v1/accounts') {
      return Promise.resolve(jsonResponse({ items: [account('acc-1')], next_cursor: null }));
    }
    if (pathname === '/api/v1/proxies') return Promise.resolve(jsonResponse({ proxies: [] }));
    return Promise.resolve(jsonResponse(account('acc-1')));
  });
}

const guardWith = (enabled: boolean) => () =>
  jsonResponse({ running: true, stalled: false, backups: { session_vault_enabled: enabled } });

test('switched-off copies put a warning with a Settings link on the page', async () => {
  routeApi(guardWith(false));
  renderWithClient(<AccountsPage />);

  const banner = await screen.findByTestId('session-copies-warning');
  expect(banner).toHaveTextContent('Копии ключей аккаунтов выключены');
  expect(screen.getByRole('link', { name: 'Открыть настройки' })).toHaveAttribute(
    'href',
    '/settings',
  );
});

test('enabled copies leave the page without the banner', async () => {
  routeApi(guardWith(true));
  renderWithClient(<AccountsPage />);

  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
  expect(screen.queryByTestId('session-copies-warning')).toBeNull();
});

test('a failed guard read shows no banner', async () => {
  routeApi(() => jsonResponse({ code: 'internal_error' }, 500));
  renderWithClient(<AccountsPage />);

  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
  expect(screen.queryByTestId('session-copies-warning')).toBeNull();
});
