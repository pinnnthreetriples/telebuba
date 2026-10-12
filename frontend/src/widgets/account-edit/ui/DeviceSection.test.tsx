import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { DeviceSection } from './DeviceSection';

const ACCOUNT: AccountRead = {
  account_id: 'acc-1',
  status: 'alive',
  phone: '+79051184490',
  created_at: 'now',
  updated_at: 'now',
  devices: ['desktop'],
};

// The card is a collapsed CollapsibleCard: its body is `hidden` until opened.
async function renderSection(account: AccountRead = ACCOUNT) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <DeviceSection account={account} />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByText('Device fingerprint'));
}

function deviceRequests(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => new URL(request.url).pathname === '/api/v1/accounts/acc-1/devices');
}

// The one request a step sent; fails loudly when there is none.
function only(requests: Request[]): Request {
  const [request] = requests;
  if (requests.length !== 1 || !request)
    throw new Error(`expected one request, got ${String(requests.length)}`);
  return request;
}

function answer(body: unknown, status = 200): void {
  vi.mocked(fetch).mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  );
}

test('the card lists the devices and adds the missing mobile only on a click', async () => {
  answer({ ...ACCOUNT, devices: ['desktop', 'mobile'] });
  await renderSection();
  expect(screen.getByText('ПК')).toBeInTheDocument();
  expect(deviceRequests()).toHaveLength(0);

  await userEvent.click(screen.getByRole('button', { name: 'Добавить мобильное устройство' }));

  const request = only(deviceRequests());
  expect(request.method).toBe('POST');
  expect(await request.clone().json()).toEqual({ device_type: 'mobile' });
});

test('a mobile-only account is offered a desktop', async () => {
  await renderSection({ ...ACCOUNT, devices: ['mobile'] });

  expect(screen.getByText('Телефон')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Добавить ПК' })).toBeInTheDocument();
});

test('an account with both devices has nothing to add', async () => {
  await renderSection({ ...ACCOUNT, devices: ['desktop', 'mobile'] });

  expect(screen.queryByRole('button', { name: /Добавить/ })).toBeNull();
});

test('a refusal is shown in place with its reason', async () => {
  answer({ error: { code: 'bad_request', message: 'device_twofa_missing' } }, 400);
  await renderSection();

  await userEvent.click(screen.getByRole('button', { name: 'Добавить мобильное устройство' }));

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Аккаунт защищён облачным паролем, а он не сохранён.',
  );
});
