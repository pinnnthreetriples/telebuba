import { screen, waitFor } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { AccountsPage } from './AccountsPage';
import { account, jsonResponse, renderWithClient } from './AccountsPage.test-helpers';

// The Accounts page carries the fleet's probe warning: the daily cap and the operator's
// out-of-turn checks. Its own behaviour is pinned in `SpamBudgetWarning.test.tsx`; this says
// the page mounts it and that a quiet fleet leaves the page as it was.
function routeApi(budget: unknown) {
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/spam-budget') return Promise.resolve(jsonResponse(budget));
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

const BUDGET = {
  probes_24h: 12,
  soft_cap: 10,
  hard_cap: 24,
  out_of_turn_24h: 1,
  out_of_turn_warn_at: 5,
  daily_cap_reached: true,
  out_of_turn_warned: false,
};

test('the page warns when the fleet has used its daily probe cap', async () => {
  routeApi(BUDGET);
  renderWithClient(<AccountsPage />);
  expect(await screen.findByTestId('spam-budget-warning')).toHaveTextContent(
    'За сутки по парку проверок: 12 из 10',
  );
});

test('a fleet within its caps leaves the page without a warning', async () => {
  routeApi({ ...BUDGET, probes_24h: 1, daily_cap_reached: false });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
  expect(screen.queryByTestId('spam-budget-warning')).toBeNull();
});
