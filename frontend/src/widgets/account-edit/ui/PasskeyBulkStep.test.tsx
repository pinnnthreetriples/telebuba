// The add wizard's passkey step on its own: the batch walks the accounts one at a
// time with a pause between them, an existing passkey counts as done, and a vault
// failure stops the batch. AddAccountModal.passkey.test.tsx wires it into the wizard.

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { PasskeyBulkStep } from './PasskeyBulkStep';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function refusal(code: string, status: number): Response {
  return jsonResponse({ error: { code: 'error', message: code, fields: null } }, status);
}

// `answers[accountId]` is what that account's create POST gets; anything else is
// an empty account list.
function serve(answers: Record<string, () => Response>): void {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const id = /\/accounts\/([^/]+)\/passkey$/.exec(new URL(request.url).pathname)?.[1];
    if (request.method === 'POST' && id) {
      const answer = answers[id];
      return Promise.resolve(answer ? answer() : jsonResponse({}));
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
}

function creates(): string[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => request.method === 'POST')
    .map((request) => new URL(request.url).pathname);
}

const created = () => jsonResponse({ account_id: 'x', has_passkey: true, vault_configured: true });

function renderStep(onDone = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <PasskeyBulkStep
        accountIds={['a', 'b']}
        sources={{ a: 'a.session', b: 'b.session' }}
        onDone={onDone}
      />
    </QueryClientProvider>,
  );
  return onDone;
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

test('the batch waits between accounts, counting down, and counts an existing passkey', async () => {
  serve({ a: created, b: () => refusal('passkey_exists', 409) });
  renderStep();
  const user = userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) });
  // Two accounts at 15–30 s apart: about a minute, said before anything starts.
  expect(screen.getByText(/с паузой 15–30 секунд/)).toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: 'Создать ключи для 2 аккаунтов' }));

  expect(await screen.findByText(/Следующий аккаунт через \d+ с/)).toBeInTheDocument();
  expect(creates()).toEqual(['/api/v1/accounts/a/passkey']);
  await act(() => vi.advanceTimersByTimeAsync(30_000));

  expect(await screen.findByText('Ключи есть у 2 из 2 аккаунтов.')).toBeInTheDocument();
  expect(creates()).toEqual(['/api/v1/accounts/a/passkey', '/api/v1/accounts/b/passkey']);
  expect(screen.getByText('ключ создан')).toBeInTheDocument();
  expect(screen.getByText('ключ уже был')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Повторить/ })).not.toBeInTheDocument();
});

test('a vault failure stops the batch and offers a retry for the rest', async () => {
  // The vault is down for the first attempt and back for the retry.
  let vaultUp = false;
  serve({ a: () => (vaultUp ? created() : refusal('passkey_vault_unreachable', 503)), b: created });
  const onDone = renderStep();
  const user = userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) });

  await user.click(screen.getByRole('button', { name: 'Создать ключи для 2 аккаунтов' }));

  expect(await screen.findByText(/остальные аккаунты пропущены/)).toBeInTheDocument();
  // No wait and no request for b: it would fail the same way.
  expect(creates()).toEqual(['/api/v1/accounts/a/passkey']);
  expect(screen.getByText('Пропущен')).toBeInTheDocument();

  vaultUp = true;
  await user.click(screen.getByRole('button', { name: 'Повторить для 2 аккаунтов' }));
  await act(() => vi.advanceTimersByTimeAsync(30_000));
  expect(await screen.findByText('Ключи есть у 2 из 2 аккаунтов.')).toBeInTheDocument();
  expect(creates()).toHaveLength(3);
  await user.click(screen.getByRole('button', { name: 'Готово' }));
  expect(onDone).toHaveBeenCalled();
});

test('Stop during the wait leaves the rest untouched', async () => {
  serve({ a: created, b: created });
  renderStep();
  const user = userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) });

  await user.click(screen.getByRole('button', { name: 'Создать ключи для 2 аккаунтов' }));
  await screen.findByText(/Следующий аккаунт через/);
  await user.click(screen.getByRole('button', { name: 'Остановить' }));

  expect(await screen.findByText('Ключи есть у 1 из 2 аккаунтов.')).toBeInTheDocument();
  expect(creates()).toEqual(['/api/v1/accounts/a/passkey']);
  expect(screen.getByRole('button', { name: 'Повторить для 1 аккаунта' })).toBeInTheDocument();
});
