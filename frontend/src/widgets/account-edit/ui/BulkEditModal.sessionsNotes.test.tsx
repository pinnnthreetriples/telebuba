import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

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

// acc-1 had no saved audit, acc-2 has two sessions it was never shown, acc-3 has a sign-in
// running: none of them is simply "done".
function routeApi() {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ items: FLEET, next_cursor: null }));
    }
    const match = /^\/api\/v1\/accounts\/([^/]+)\/sessions\/close-foreign$/.exec(pathname);
    if (match?.[1]) {
      const id = match[1];
      if (id === 'acc-3') {
        return Promise.resolve(
          jsonResponse({ error: { code: 'conflict', message: 'sign_in_in_progress' } }, 409),
        );
      }
      return Promise.resolve(
        jsonResponse({
          account_id: id,
          outcomes: [],
          closed_count: 0,
          ...(id === 'acc-1' ? { no_prior_audit: true } : { unseen_count: 2 }),
        }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });
}

test('close-all tells a first-time list, new sessions and a running sign-in per account', async () => {
  routeApi();
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
  await user.click(screen.getByRole('checkbox', { name: 'Закрыть чужие сессии этих аккаунтов' }));
  await user.click(screen.getByRole('button', { name: 'Применить к 3 аккаунтам' }));

  await waitFor(() => {
    expect(screen.getByText('3 с ошибкой')).toBeInTheDocument();
  });
  expect(screen.getByText(/Список сессий сохранён впервые — проверьте его/)).toBeInTheDocument();
  expect(
    screen.getByText(/Появились новые сессии \(2\) — проверьте и закройте ещё раз/),
  ).toBeInTheDocument();
  expect(screen.getByText(/Вход в этот аккаунт ещё идёт/)).toBeInTheDocument();
});
