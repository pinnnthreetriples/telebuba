import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { ActionsSection } from './ActionsSection';

// Own file rather than more of AccountEdit.test.tsx, which is already at ~643
// lines against the 700-line cap.

const ACCOUNT: AccountRead = {
  account_id: 'acc-1',
  label: 'Main',
  status: 'alive',
  phone: '+79051184490',
  created_at: 'now',
  updated_at: 'now',
};

function renderSection() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ActionsSection account={ACCOUNT} onBack={vi.fn()} />
    </QueryClientProvider>,
  );
}

function checks(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => new URL(request.url).pathname === '/api/v1/accounts/check');
}

test('the alive check is locked while it is in flight (no second Telegram round trip)', async () => {
  // The button had no `disabled` while its sibling reset button did: a second
  // click took over the mutation's one callback slot, so the FIRST check's
  // verdict and its invalidate() were dropped — plus a wasted RPC.
  let releaseCheck!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation((input) => {
    if (new URL((input as Request).url).pathname === '/api/v1/accounts/check') {
      return new Promise<Response>((resolve) => {
        releaseCheck = resolve;
      });
    }
    return Promise.resolve(new Response('{}', { headers: { 'Content-Type': 'application/json' } }));
  });
  renderSection();

  // The card is a collapsed CollapsibleCard: its body is `hidden` until opened.
  await userEvent.click(screen.getByText('Действия'));
  const button = screen.getByRole('button', { name: 'Проверить, живой ли аккаунт' });
  await userEvent.click(button);

  await waitFor(() => {
    expect(button).toBeDisabled();
  });
  await userEvent.click(button);
  expect(checks()).toHaveLength(1);

  releaseCheck(
    new Response(JSON.stringify({ account_id: 'acc-1', status: 'alive' }), {
      headers: { 'Content-Type': 'application/json' },
    }),
  );
  await waitFor(() => {
    expect(screen.getByText('Аккаунт живой')).toBeInTheDocument();
  });
  expect(button).toBeEnabled();
});
