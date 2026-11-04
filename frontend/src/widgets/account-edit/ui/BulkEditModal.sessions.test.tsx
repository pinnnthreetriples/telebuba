import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead, SessionCloseResult } from '@/shared/api';

import { BulkEditModal } from './BulkEditModal';

function account(id: string, name: string): AccountRead {
  return {
    account_id: id,
    status: 'alive',
    first_name: name,
    phone: `+7999000000${id.slice(-1)}`,
    created_at: 'now',
    updated_at: 'now',
  };
}

const FLEET = [account('acc-1', 'Иван'), account('acc-2', 'Пётр'), account('acc-3', 'Анна')];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function result(accountId: string, outcomes: SessionCloseResult['outcomes']): SessionCloseResult {
  return {
    account_id: accountId,
    outcomes,
    closed_count: outcomes.filter((o) => o.status === 'closed').length,
  };
}

// Each account answers its own close-foreign: acc-1 closes one session, acc-2 is refused by
// Telegram (its session is too young), acc-3 had nothing foreign.
function routeApi(): string[] {
  const closed: string[] = [];
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ items: FLEET, next_cursor: null }));
    }
    const match = /^\/api\/v1\/accounts\/([^/]+)\/sessions\/close-foreign$/.exec(pathname);
    if (match?.[1]) {
      const id = match[1];
      closed.push(id);
      const answers: Record<string, SessionCloseResult> = {
        'acc-1': result(id, [{ hash: '2', status: 'closed' }]),
        'acc-2': result(id, [{ hash: '3', status: 'too_new', retry_after_seconds: 14400 }]),
        'acc-3': result(id, []),
      };
      return Promise.resolve(jsonResponse(answers[id]));
    }
    return Promise.resolve(jsonResponse({}));
  });
  return closed;
}

test('closing foreign sessions needs the confirmation, then runs per account and tells each outcome', async () => {
  const closed = routeApi();
  const user = userEvent.setup();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <BulkEditModal account={FLEET[0]!} onClose={vi.fn()} />
    </QueryClientProvider>,
  );

  await user.click(screen.getByRole('button', { name: 'Добавить аккаунты' }));
  await screen.findByRole('checkbox', { name: 'Выбрать все (3)' });
  await user.click(screen.getByRole('checkbox', { name: 'Выбрать все (3)' }));
  await user.click(screen.getByRole('button', { name: 'Добавить (3)' }));
  await user.click(screen.getByRole('tab', { name: 'Сессии' }));

  const apply = screen.getByRole('button', { name: 'Применить к 3 аккаунтам' });
  expect(apply).toBeDisabled();
  await user.click(screen.getByRole('checkbox', { name: 'Закрыть чужие сессии этих аккаунтов' }));
  await user.click(apply);

  await waitFor(() => {
    expect(screen.getByText('Применено 3 из 3')).toBeInTheDocument();
  });
  expect(closed).toEqual(['acc-1', 'acc-2', 'acc-3']);
  // The refused account keeps its reason, in words, with the time Telegram's rule leaves.
  expect(screen.getByText(/повторите примерно через 4 ч/)).toBeInTheDocument();
  expect(screen.getByText('1 с ошибкой')).toBeInTheDocument();
});

test('re-keying needs the confirmation, then runs per account and keeps a refusal as that row', async () => {
  const rekeyed: string[] = [];
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ items: FLEET, next_cursor: null }));
    }
    const match = /^\/api\/v1\/accounts\/([^/]+)\/rekey$/.exec(pathname);
    if (match?.[1] && request.method === 'POST') {
      rekeyed.push(match[1]);
      return Promise.resolve(
        match[1] === 'acc-2'
          ? jsonResponse({ error: { code: 'bad_request', message: 'rekey_twofa_missing' } }, 400)
          : jsonResponse({ account_id: match[1], rekeyed_at: '2026-11-02T10:00:00+00:00' }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });
  const user = userEvent.setup();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <BulkEditModal account={FLEET[0]!} onClose={vi.fn()} />
    </QueryClientProvider>,
  );

  await user.click(screen.getByRole('button', { name: 'Добавить аккаунты' }));
  await screen.findByRole('checkbox', { name: 'Выбрать все (3)' });
  await user.click(screen.getByRole('checkbox', { name: 'Выбрать все (3)' }));
  await user.click(screen.getByRole('button', { name: 'Добавить (3)' }));
  await user.click(screen.getByRole('tab', { name: 'Замена ключа' }));

  const apply = screen.getByRole('button', { name: 'Применить к 3 аккаунтам' });
  expect(apply).toBeDisabled();
  await user.click(screen.getByRole('checkbox', { name: 'Заменить ключ сессии этих аккаунтов' }));
  await user.click(apply);

  await waitFor(() => {
    expect(screen.getByText('Применено 3 из 3')).toBeInTheDocument();
  });
  // Each account in turn; the refused one does not stop the others.
  expect(rekeyed).toEqual(['acc-1', 'acc-2', 'acc-3']);
  expect(screen.getByText('1 с ошибкой')).toBeInTheDocument();
});
