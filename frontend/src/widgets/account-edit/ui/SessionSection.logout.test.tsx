import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { SessionSection } from './SessionSection';

const ACCOUNT: AccountRead = {
  account_id: 'acc-1',
  status: 'unauthorized',
  devices: ['desktop', 'mobile'],
  second_session_name: 'acc-1_mobile',
  created_at: 'now',
  updated_at: 'now',
};

function logouts(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => new URL(request.url).pathname === '/api/v1/accounts/acc-1/logout');
}

test('logging out asks first and names the second device that ends with it', async () => {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify(ACCOUNT), { headers: { 'Content-Type': 'application/json' } }),
    ),
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <SessionSection account={ACCOUNT} />
    </QueryClientProvider>,
  );

  // The card is a collapsed CollapsibleCard: its body is `hidden` until opened.
  await userEvent.click(screen.getByText('Сессия'));
  await userEvent.click(screen.getByRole('button', { name: 'Выйти из аккаунта' }));
  expect(await screen.findByText('Телефон: второе устройство')).toBeVisible();
  expect(logouts()).toHaveLength(0);

  await userEvent.click(screen.getByRole('button', { name: 'Выйти' }));

  await waitFor(() => {
    expect(logouts()).toHaveLength(1);
  });
});
