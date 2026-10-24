import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountPasskeyView, AccountRead } from '@/shared/api';

import { PasskeySection } from './PasskeySection';

const ACCOUNT: AccountRead = {
  account_id: 'acc-1',
  status: 'alive',
  phone: '+79051184490',
  created_at: 'now',
  updated_at: 'now',
  devices: ['desktop'],
};
const URL_PATH = '/api/v1/accounts/acc-1/passkey';

function view(overrides: Partial<AccountPasskeyView> = {}): AccountPasskeyView {
  return { account_id: 'acc-1', has_passkey: false, vault_configured: true, ...overrides };
}

// Every passkey route answers the same view; GET returns `current`, a write `next`.
function serve(current: AccountPasskeyView, next: AccountPasskeyView = current): void {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const body = request.method === 'GET' ? current : next;
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  });
}

function passkeyWrites(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => new URL(request.url).pathname.startsWith(URL_PATH))
    .filter((request) => request.method !== 'GET');
}

async function renderSection() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <PasskeySection account={ACCOUNT} />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByText('Ключ доступа'));
}

test('an account without a passkey is offered one, created only on a click', async () => {
  serve(view(), view({ has_passkey: true, created_at: '2026-10-24T12:00:00+00:00' }));
  await renderSection();

  const create = await screen.findByRole('button', { name: 'Создать ключ доступа' });
  expect(passkeyWrites()).toHaveLength(0);
  await userEvent.click(create);

  const [request] = passkeyWrites();
  expect(request?.method).toBe('POST');
  expect(new URL(request?.url ?? '').pathname).toBe(URL_PATH);
});

test('an account with a passkey can sign in with it or remove it after confirming', async () => {
  serve(view({ has_passkey: true, created_at: '2026-10-24T12:00:00+00:00' }));
  await renderSection();

  await userEvent.click(await screen.findByRole('button', { name: 'Войти по ключу' }));
  expect(new URL(passkeyWrites()[0]?.url ?? '').pathname).toBe(`${URL_PATH}/login`);

  await userEvent.click(screen.getByRole('button', { name: 'Удалить ключ' }));
  expect(await screen.findByText('Удалить ключ доступа?')).toBeInTheDocument();
  expect(passkeyWrites()).toHaveLength(1);
  const buttons = screen.getAllByRole('button', { name: 'Удалить ключ' });
  await userEvent.click(buttons[buttons.length - 1] as HTMLElement);

  expect(passkeyWrites().map((request) => request.method)).toEqual(['POST', 'DELETE']);
});

test('without a key vault the card says so and offers nothing', async () => {
  serve(view({ vault_configured: false }));
  await renderSection();

  expect(await screen.findByText(/Сейф ключей не настроен/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Создать ключ доступа' })).toBeNull();
});
