import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRekeyView } from '@/shared/api';

import { SessionRekeyControls } from './SessionRekeyControls';

const URL_PATH = '/api/v1/accounts/acc-1/rekey';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

// GET answers `current`; the POST that re-keys answers `next`, or a refusal.
function serve(
  current: AccountRekeyView,
  next: AccountRekeyView = current,
  refusal?: string,
): void {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (request.method === 'POST' && refusal) {
      return Promise.resolve(json({ error: { code: 'bad_request', message: refusal } }, 400));
    }
    return Promise.resolve(json(request.method === 'GET' ? current : next));
  });
}

function posts(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => new URL(request.url).pathname === URL_PATH && request.method === 'POST');
}

function renderControls(props: { hasProxy?: boolean } = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <SessionRekeyControls accountId="acc-1" open hasProxy={props.hasProxy ?? true} />
    </QueryClientProvider>,
  );
}

test('nothing happens until the operator confirms, and then the account is re-keyed', async () => {
  serve(
    { account_id: 'acc-1' },
    {
      account_id: 'acc-1',
      rekeyed_at: '2026-11-02T10:00:00+00:00',
      closed_at: '2026-11-02T10:00:09+00:00',
    },
  );
  renderControls();

  expect(await screen.findByText('Ключ не заменялся')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Заменить ключ сессии' }));
  expect(await screen.findByText('Заменить ключ этой сессии?')).toBeVisible();
  expect(posts()).toHaveLength(0);

  const confirm = screen.getAllByRole('button', { name: 'Заменить ключ сессии' });
  await userEvent.click(confirm[confirm.length - 1] as HTMLElement);

  expect(await screen.findByText(/Старая сессия закрыта/)).toBeVisible();
  expect(posts()).toHaveLength(1);
});

test('an old session Telegram would not end yet says when it closes', async () => {
  serve({
    account_id: 'acc-1',
    rekeyed_at: '2026-11-02T10:00:00+00:00',
    close_after: '2026-11-03T10:01:00+00:00',
  });
  renderControls();

  expect(await screen.findByText(/Старая сессия закроется после/)).toBeVisible();
});

test('an account without a proxy cannot be re-keyed', async () => {
  serve({ account_id: 'acc-1' });
  renderControls({ hasProxy: false });

  expect(await screen.findByRole('button', { name: 'Заменить ключ сессии' })).toBeDisabled();
});

test('a refusal keeps the dialog open and posts once', async () => {
  serve({ account_id: 'acc-1' }, { account_id: 'acc-1' }, 'rekey_twofa_missing');
  renderControls();

  await userEvent.click(await screen.findByRole('button', { name: 'Заменить ключ сессии' }));
  const confirm = screen.getAllByRole('button', { name: 'Заменить ключ сессии' });
  await userEvent.click(confirm[confirm.length - 1] as HTMLElement);

  expect(posts()).toHaveLength(1);
  expect(screen.getByText('Заменить ключ этой сессии?')).toBeVisible();
});
