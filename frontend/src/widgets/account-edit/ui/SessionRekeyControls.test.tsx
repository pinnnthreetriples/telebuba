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

async function confirmRekey(): Promise<void> {
  await userEvent.click(await screen.findByRole('button', { name: 'Заменить ключ сессии' }));
  const confirm = screen.getAllByRole('button', { name: 'Заменить ключ сессии' });
  await userEvent.click(confirm[confirm.length - 1] as HTMLElement);
}

test('a failure closes the dialog and the card says why and that nothing changed', async () => {
  serve({ account_id: 'acc-1' }, { account_id: 'acc-1' }, 'rekey_timeout');
  renderControls();

  await confirmRekey();

  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Ключ не заменён, сессия осталась прежней.');
  expect(alert).toHaveTextContent('Telegram не подтвердил новый вход за 75 секунд');
  expect(screen.queryByText('Заменить ключ этой сессии?')).not.toBeInTheDocument();
  expect(posts()).toHaveLength(1);
});

test('a flood wait names the seconds Telegram asked for', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (request.method === 'POST') {
      return Promise.resolve(
        json(
          {
            error: {
              code: 'bad_request',
              message: 'rekey_flood_wait',
              fields: { retry_after_seconds: '42' },
            },
          },
          429,
        ),
      );
    }
    return Promise.resolve(json({ account_id: 'acc-1' }));
  });
  renderControls();

  await confirmRekey();

  expect(await screen.findByRole('alert')).toHaveTextContent('подождать 42 с');
});

test('a re-key whose old session is not closed yet says when it will be', async () => {
  serve(
    { account_id: 'acc-1' },
    {
      account_id: 'acc-1',
      rekeyed_at: '2026-11-02T10:00:00+00:00',
      close_after: '2026-11-03T10:01:00+00:00',
    },
  );
  renderControls();

  await confirmRekey();

  expect(await screen.findByRole('status')).toHaveTextContent(
    'Ключ заменён. Telegram пока не даёт закрыть старую сессию',
  );
});

test('a completed re-key says the old session is closed', async () => {
  serve(
    { account_id: 'acc-1' },
    {
      account_id: 'acc-1',
      rekeyed_at: '2026-11-02T10:00:00+00:00',
      closed_at: '2026-11-02T10:00:09+00:00',
    },
  );
  renderControls();

  await confirmRekey();

  expect(await screen.findByText('Ключ заменён, старая сессия закрыта.')).toBeVisible();
});

test('without a proxy the card says why the button is off', async () => {
  serve({ account_id: 'acc-1' });
  renderControls({ hasProxy: false });

  expect(await screen.findByText(/Сначала назначьте аккаунту прокси/)).toBeVisible();
});
