import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { BulkEditModal } from './BulkEditModal';

const ACC1: AccountRead = {
  account_id: 'acc-1',
  status: 'alive',
  first_name: 'Иван',
  created_at: 'now',
  updated_at: 'now',
};
const FLEET: AccountRead[] = [
  ACC1,
  { ...ACC1, account_id: 'acc-2', first_name: 'Пётр' },
  { ...ACC1, account_id: 'acc-3', first_name: 'Анна' },
];

type Call = { url: string; body: Record<string, unknown> | null; fileName: string | null };

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

// Every account owns one channel, named after it, so a post batch can be checked
// against the channels each account actually holds.
function routeApi(): Call[] {
  const calls: Call[] = [];
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (request.method === 'GET') {
      if (pathname === '/api/v1/accounts') {
        return jsonResponse({ items: FLEET, next_cursor: null });
      }
      const owner = /\/accounts\/(?<id>[^/]+)\/channels$/.exec(pathname)?.groups?.id;
      if (owner) {
        return jsonResponse({
          items: [{ channel_id: `ch-${owner}`, title: 'Канал', username: null }],
        });
      }
      return jsonResponse({});
    }
    let body: Record<string, unknown> | null = null;
    let fileName: string | null = null;
    const type = request.headers.get('content-type') ?? '';
    if (type.includes('json')) {
      body = (await request.clone().json()) as Record<string, unknown>;
    } else {
      const form = await request.clone().formData();
      const file = form.get('file');
      fileName = file instanceof File ? file.name : null;
      body = Object.fromEntries(
        [...form.entries()].filter(([, value]) => typeof value === 'string'),
      );
    }
    calls.push({ url: pathname, body, fileName });
    return jsonResponse({ status: 'ok', action_type: 'x', channel_id: 'ch-new', settings: {} });
  });
  return calls;
}

function renderModal() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <BulkEditModal account={ACC1} onClose={vi.fn()} />
    </QueryClientProvider>,
  );
}

async function selectWholeFleet(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Добавить аккаунты' }));
  await screen.findByRole('checkbox', { name: 'Выбрать все (3)' });
  await user.click(screen.getByRole('checkbox', { name: 'Выбрать все (3)' }));
  await user.click(screen.getByRole('button', { name: 'Добавить (3)' }));
}

test('a public bulk create numbers the handle per account and carries one avatar', async () => {
  const calls = routeApi();
  const user = userEvent.setup();
  renderModal();

  await selectWholeFleet(user);
  await user.click(screen.getByRole('tab', { name: 'Каналы' }));
  await user.type(screen.getByRole('textbox', { name: 'Название' }), 'Скидки');
  await user.click(screen.getByRole('checkbox', { name: 'Публичный канал' }));

  const handle = screen.getByRole('textbox', { name: 'Юзернейм' });
  await user.type(handle, 'skidki_{{n}');
  const avatar = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (!avatar) throw new Error('no avatar input');
  fireEvent.change(avatar, {
    target: { files: [new File(['x'], 'logo.jpg', { type: 'image/jpeg' })] },
  });

  await user.click(screen.getByRole('button', { name: 'Применить к 3 аккаунтам' }));
  await waitFor(() => {
    expect(screen.getByText('Применено 3 из 3')).toBeInTheDocument();
  });

  const created = calls.filter((call) => /\/channels$/.test(call.url));
  expect(created.map((call) => call.body?.username)).toEqual(['skidki_1', 'skidki_2', 'skidki_3']);
  expect(created.every((call) => call.body?.title === 'Скидки')).toBe(true);
  expect(
    calls.filter((call) => call.url.endsWith('/photo') && call.fileName === 'logo.jpg'),
  ).toHaveLength(3);
});

test('a public create without {n} stays blocked for a batch, allowed for one account', async () => {
  routeApi();
  const user = userEvent.setup();
  renderModal();

  await user.click(screen.getByRole('tab', { name: 'Каналы' }));
  await user.type(screen.getByRole('textbox', { name: 'Название' }), 'Скидки');
  await user.click(screen.getByRole('checkbox', { name: 'Публичный канал' }));
  await user.type(screen.getByRole('textbox', { name: 'Юзернейм' }), 'skidki');

  // One account, one handle: nothing to collide with.
  expect(screen.getByRole('button', { name: 'Применить к 1 аккаунту' })).toBeEnabled();

  await selectWholeFleet(user);
  expect(screen.getByRole('button', { name: 'Применить к 3 аккаунтам' })).toBeDisabled();
});

test('a post goes to every channel of every selected account', async () => {
  const calls = routeApi();
  const user = userEvent.setup();
  renderModal();

  await selectWholeFleet(user);
  await user.click(screen.getByRole('tab', { name: 'Каналы' }));
  await user.click(screen.getByRole('radio', { name: 'Пост в каналы' }));
  await user.type(screen.getByRole('textbox', { name: 'Текст поста…' }), 'Привет');

  await user.click(screen.getByRole('button', { name: 'Применить к 3 аккаунтам' }));
  await waitFor(() => {
    expect(screen.getByText('Применено 3 из 3')).toBeInTheDocument();
  });

  expect(calls.filter((call) => call.url.endsWith('/posts')).map((call) => call.url)).toEqual([
    '/api/v1/accounts/acc-1/channels/ch-acc-1/posts',
    '/api/v1/accounts/acc-2/channels/ch-acc-2/posts',
    '/api/v1/accounts/acc-3/channels/ch-acc-3/posts',
  ]);
});

test('privacy sends only the ticked rows', async () => {
  const calls = routeApi();
  const user = userEvent.setup();
  renderModal();

  await selectWholeFleet(user);
  await user.click(screen.getByRole('tab', { name: 'Приватность' }));
  expect(screen.getByRole('button', { name: 'Применить к 3 аккаунтам' })).toBeDisabled();

  await user.click(screen.getByRole('checkbox', { name: 'Фото профиля' }));
  await user.click(screen.getByRole('radio', { name: 'Фото профиля: Контакты' }));
  await user.click(screen.getByRole('button', { name: 'Применить к 3 аккаунтам' }));

  await waitFor(() => {
    expect(screen.getByText('Применено 3 из 3')).toBeInTheDocument();
  });
  const writes = calls.filter((call) => call.url.endsWith('/privacy'));
  expect(writes).toHaveLength(3);
  expect(writes.every((call) => JSON.stringify(call.body) === '{"profile_photo":"contacts"}')).toBe(
    true,
  );
});

// A handle 32 chars long with `1` is 33 with `10`: the tenth account onward would
// have been refused by Telegram after nine channels had already been created, so
// the check has to run at BOTH ends of the batch. Needs a fleet past nine.
test('a handle that overflows at the tenth account is blocked before the batch runs', async () => {
  const dozen = Array.from({ length: 12 }, (_, i) => ({
    ...ACC1,
    account_id: `acc-${String(i + 1)}`,
    first_name: `Аккаунт ${String(i + 1)}`,
  }));
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/accounts' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ items: dozen, next_cursor: null }));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x' }));
  });
  const user = userEvent.setup();
  renderModal();

  await user.click(screen.getByRole('button', { name: 'Добавить аккаунты' }));
  await screen.findByRole('checkbox', { name: 'Выбрать все (12)' });
  await user.click(screen.getByRole('checkbox', { name: 'Выбрать все (12)' }));
  await user.click(screen.getByRole('button', { name: 'Добавить (12)' }));

  await user.click(screen.getByRole('tab', { name: 'Каналы' }));
  await user.type(screen.getByRole('textbox', { name: 'Название' }), 'Скидки');
  await user.click(screen.getByRole('checkbox', { name: 'Публичный канал' }));
  const handle = screen.getByRole('textbox', { name: 'Юзернейм' });

  // 29 letters + `{n}`: 30 chars at account 1, 31 at account 12 — both fit.
  fireEvent.change(handle, { target: { value: `${'a'.repeat(29)}{n}` } });
  expect(screen.getByRole('button', { name: 'Применить к 12 аккаунтам' })).toBeEnabled();

  // 31 letters + `{n}`: 32 chars at account 1 (the ceiling) but 33 at account 12.
  fireEvent.change(handle, { target: { value: `${'a'.repeat(31)}{n}` } });
  expect(screen.getByRole('button', { name: 'Применить к 12 аккаунтам' })).toBeDisabled();
});

test('a post longer than the caption cap is blocked once media is attached', async () => {
  routeApi();
  const user = userEvent.setup();
  renderModal();

  await user.click(screen.getByRole('tab', { name: 'Каналы' }));
  await user.click(screen.getByRole('radio', { name: 'Пост в каналы' }));
  const composer = screen.getByRole('textbox', { name: 'Текст поста…' });
  fireEvent.change(composer, { target: { value: 'x'.repeat(1500) } });
  expect(screen.getByRole('button', { name: 'Применить к 1 аккаунту' })).toBeEnabled();

  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (!input) throw new Error('no file input');
  fireEvent.change(input, {
    target: { files: [new File(['x'], 'pic.jpg', { type: 'image/jpeg' })] },
  });

  // 1500 chars fit a plain post but not a caption under media.
  expect(screen.getByText('1500/1024')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Применить к 1 аккаунту' })).toBeDisabled();
});

test('an account with no channels is reported, not counted as done', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ items: FLEET, next_cursor: null }));
    }
    if (request.method === 'GET') return Promise.resolve(jsonResponse({ items: [] }));
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x' }));
  });
  const user = userEvent.setup();
  renderModal();

  await user.click(screen.getByRole('tab', { name: 'Каналы' }));
  await user.click(screen.getByRole('radio', { name: 'Пост в каналы' }));
  await user.type(screen.getByRole('textbox', { name: 'Текст поста…' }), 'Привет');
  await user.click(screen.getByRole('button', { name: 'Применить к 1 аккаунту' }));

  await waitFor(() => {
    expect(screen.getByText('У аккаунта нет каналов')).toBeInTheDocument();
  });
  expect(screen.getByText('1 с ошибкой')).toBeInTheDocument();
});
