import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { BulkEditModal } from './BulkEditModal';

const ACC1: AccountRead = {
  account_id: 'acc-1',
  status: 'alive',
  first_name: 'Иван',
  phone: '+79990000001',
  created_at: 'now',
  updated_at: 'now',
};

const FLEET: AccountRead[] = [
  ACC1,
  {
    account_id: 'acc-2',
    status: 'alive',
    first_name: 'Пётр',
    phone: '+79990000002',
    created_at: 'now',
    updated_at: 'now',
  },
  {
    account_id: 'acc-3',
    status: 'alive',
    first_name: 'Анна',
    phone: '+79990000003',
    created_at: 'now',
    updated_at: 'now',
  },
];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

type ProfileBody = { account_id: string; bio?: string; first_name?: string; last_name?: string };

// The generated client calls `fetch(new Request(...))`, so the payload rides the
// Request rather than an init argument — read it here, where the body is still
// unconsumed, instead of off the recorded call.
//
// `profileFails` refuses exactly one account, so a batch can be watched carrying
// on past a refusal — the whole reason the rows keep their own state.
function routeApi(profileFails?: string): ProfileBody[] {
  const bodies: ProfileBody[] = [];
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts' && request.method === 'GET') {
      return jsonResponse({ items: FLEET, next_cursor: null });
    }
    if (pathname === '/api/v1/accounts/profile') {
      const body = (await request.clone().json()) as ProfileBody;
      bodies.push(body);
      if (body.account_id === profileFails) {
        return jsonResponse(
          { error: { code: 'flood_wait', message: 'flood_wait', details: [] } },
          429,
        );
      }
      return jsonResponse({ ...ACC1, account_id: body.account_id });
    }
    return jsonResponse({});
  });
  return bodies;
}

function renderModal() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <BulkEditModal account={ACC1} onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onClose };
}

test('applies only the ticked fields, to every picked account', async () => {
  const bodies = routeApi();
  const user = userEvent.setup();
  renderModal();

  await user.click(screen.getByRole('button', { name: 'Добавить аккаунты' }));
  await screen.findByRole('checkbox', { name: /Выбрать все/ });
  await user.click(screen.getByRole('checkbox', { name: 'Выбрать все (3)' }));
  await user.click(screen.getByRole('button', { name: 'Добавить (3)' }));

  expect(screen.getByText('3 аккаунта выбрано')).toBeInTheDocument();

  await user.click(screen.getByRole('checkbox', { name: 'Описание (bio)' }));
  await user.type(screen.getByRole('textbox', { name: 'Описание (bio)' }), 'Новое био');
  await user.click(screen.getByRole('button', { name: 'Применить к 3 аккаунтам' }));

  await waitFor(() => {
    expect(bodies).toHaveLength(3);
  });
  expect(bodies.map((body) => body.account_id)).toEqual(['acc-1', 'acc-2', 'acc-3']);
  // The name was never ticked, so it is absent — not sent empty, which would
  // have cleared it, and not sent stale, which would have overwritten it.
  expect(bodies.every((body) => body.first_name === undefined)).toBe(true);
  expect(bodies.every((body) => body.bio === 'Новое био')).toBe(true);
});

test('a refused account does not abandon the rest of the batch', async () => {
  const bodies = routeApi('acc-1');
  const user = userEvent.setup();
  renderModal();

  await user.click(screen.getByRole('button', { name: 'Добавить аккаунты' }));
  await screen.findByRole('checkbox', { name: 'Выбрать все (3)' });
  await user.click(screen.getByRole('checkbox', { name: 'Выбрать все (3)' }));
  await user.click(screen.getByRole('button', { name: 'Добавить (3)' }));

  await user.click(screen.getByRole('checkbox', { name: 'Имя' }));
  await user.type(screen.getByRole('textbox', { name: 'Имя' }), 'Алекс');
  await user.click(screen.getByRole('button', { name: 'Применить к 3 аккаунтам' }));

  await waitFor(() => {
    expect(screen.getByText('Применено 3 из 3')).toBeInTheDocument();
  });
  expect(bodies).toHaveLength(3);
  expect(screen.getByText('1 с ошибкой')).toBeInTheDocument();
});

test('an empty first name blocks the apply, an empty bio does not', async () => {
  routeApi();
  const user = userEvent.setup();
  renderModal();

  await user.click(screen.getByRole('checkbox', { name: 'Имя' }));
  expect(screen.getByRole('button', { name: 'Применить к 1 аккаунту' })).toBeDisabled();
  expect(screen.getByRole('alert')).toHaveTextContent('Укажите имя');

  await user.click(screen.getByRole('checkbox', { name: 'Имя' }));
  await user.click(screen.getByRole('checkbox', { name: 'Описание (bio)' }));

  // A ticked-but-empty bio is the only way to wipe one across a batch, so it
  // stays applicable — and says so.
  expect(screen.getByRole('button', { name: 'Применить к 1 аккаунту' })).toBeEnabled();
  expect(screen.getByText('Пустое поле сотрёт значение у всех выбранных')).toBeInTheDocument();
});
