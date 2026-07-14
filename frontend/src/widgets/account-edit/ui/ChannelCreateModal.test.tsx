import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { ChannelsTab } from './ChannelsTab';

// The create dialog is exercised through ChannelsTab: the lifted onCreated
// callback (close create → open editor) and the list invalidation are the
// tab's wiring, so the integration is what matters.

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function routeApi({
  onCreate,
  onCheck,
}: {
  onCreate?: () => Response;
  onCheck?: (username: string) => Response;
} = {}) {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    const { pathname } = url;
    if (pathname === '/api/v1/accounts/acc-1/channels' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
    }
    if (pathname === '/api/v1/accounts/acc-1/channels' && request.method === 'POST') {
      return Promise.resolve(
        onCreate?.() ??
          jsonResponse({
            status: 'ok',
            action_type: 'channel_create',
            account_id: 'acc-1',
            channel_id: '789',
          }),
      );
    }
    if (pathname === '/api/v1/accounts/acc-1/channel-username-check') {
      return Promise.resolve(
        onCheck?.(url.searchParams.get('username') ?? '') ??
          jsonResponse({ available: true, code: null }),
      );
    }
    if (pathname === '/api/v1/accounts/acc-1/channels/789' && request.method === 'GET') {
      return Promise.resolve(
        jsonResponse({
          channel_id: '789',
          title: 'Новости',
          username: null,
          participants_count: 1,
          about: '',
        }),
      );
    }
    if (pathname === '/api/v1/accounts/acc-1/channels/789/posts') {
      return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
}

async function openCreate() {
  renderWithClient(<ChannelsTab accountId="acc-1" />);
  await userEvent.click(await screen.findByText('Создать канал'));
  await screen.findByText('Новый канал');
}

function createPosts(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter(
      (request) =>
        new URL(request.url).pathname === '/api/v1/accounts/acc-1/channels' &&
        request.method === 'POST',
    );
}

function listGets(): number {
  return vi.mocked(fetch).mock.calls.filter(([input]) => {
    const request = input as Request;
    return (
      new URL(request.url).pathname === '/api/v1/accounts/acc-1/channels' &&
      request.method === 'GET'
    );
  }).length;
}

test('the happy path posts the request and hands off into the editor', async () => {
  routeApi();
  await openCreate();

  await userEvent.type(screen.getByLabelText('Название'), 'Новости');
  await userEvent.click(screen.getByText('Создать'));

  await waitFor(() => {
    expect(createPosts()).toHaveLength(1);
  });
  const body = (await (createPosts()[0] as Request).clone().json()) as Record<string, unknown>;
  expect(body).toEqual({ title: 'Новости', about: '', username: null });

  // onCreated lifts the created channel_id into the editor (create closes).
  expect(await screen.findByDisplayValue('Новости')).toBeInTheDocument();
  expect(screen.queryByText('Новый канал')).not.toBeInTheDocument();
  // The channels list was invalidated (initial GET + refetch).
  await waitFor(() => {
    expect(listGets()).toBe(2);
  });
});

test('a public channel sends the username and the debounced check shows taken/free hints', async () => {
  routeApi({
    onCheck: (username) =>
      username === 'newshub'
        ? jsonResponse({ available: false, code: 'channel_username_occupied' })
        : jsonResponse({ available: true, code: null }),
  });
  await openCreate();

  await userEvent.click(screen.getByText('Публичный канал'));
  // The label's text includes the visual '@' prefix span → match loosely.
  const usernameInput = screen.getByLabelText(/Юзернейм/);

  // Too short → the format hint shows without hitting the API.
  await userEvent.type(usernameInput, 'ab');
  expect(
    screen.getByText('Юзернейм: 5–32 символа, латиница, цифры и _, начинается с буквы'),
  ).toBeInTheDocument();

  await userEvent.clear(usernameInput);
  await userEvent.type(usernameInput, 'newshub');
  // The probe fires after the ~500ms debounce and the verdict translates.
  expect(await screen.findByText('Юзернейм уже занят', {}, { timeout: 3000 })).toBeInTheDocument();

  await userEvent.clear(usernameInput);
  await userEvent.type(usernameInput, 'freshname');
  expect(await screen.findByText('Юзернейм свободен', {}, { timeout: 3000 })).toBeInTheDocument();

  await userEvent.type(screen.getByLabelText('Название'), 'Новости');
  await userEvent.click(screen.getByText('Создать'));
  await waitFor(() => {
    expect(createPosts()).toHaveLength(1);
  });
  const body = (await (createPosts()[0] as Request).clone().json()) as Record<string, unknown>;
  expect(body).toEqual({ title: 'Новости', about: '', username: 'freshname' });
});

test('a stable failure code renders as translated copy', async () => {
  routeApi({
    onCreate: () =>
      jsonResponse({ error: { code: 'bad_request', message: 'channels_too_much' } }, 400),
  });
  await openCreate();

  await userEvent.type(screen.getByLabelText('Название'), 'Новости');
  await userEvent.click(screen.getByText('Создать'));

  expect(await screen.findByText('Достигнут лимит каналов на аккаунте')).toBeInTheDocument();
  expect(screen.queryByText('channels_too_much')).not.toBeInTheDocument();
  // The dialog stays open — no editor hand-off happened.
  expect(screen.getByText('Новый канал')).toBeInTheDocument();
});

test('a create-after-create failure (fields.channel_id) still refreshes the list', async () => {
  routeApi({
    onCreate: () =>
      jsonResponse(
        {
          error: {
            code: 'bad_request',
            message: 'channel_username_occupied',
            fields: { channel_id: '321' },
          },
        },
        400,
      ),
  });
  await openCreate();

  await userEvent.type(screen.getByLabelText('Название'), 'Новости');
  await userEvent.click(screen.getByText('Создать'));

  expect(await screen.findByText('Юзернейм уже занят')).toBeInTheDocument();
  // The channel exists as private despite the error — the list must re-pull.
  await waitFor(() => {
    expect(listGets()).toBe(2);
  });
});

test('the exits are locked while the create is in flight', async () => {
  let resolveCreate!: (response: Response) => void;
  routeApi();
  await openCreate();
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (
      new URL(request.url).pathname === '/api/v1/accounts/acc-1/channels' &&
      request.method === 'POST'
    ) {
      return new Promise((resolve) => {
        resolveCreate = resolve;
      });
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });

  await userEvent.type(screen.getByLabelText('Название'), 'Новости');
  await userEvent.click(screen.getByText('Создать'));

  await waitFor(() => {
    expect(screen.getByText('Отмена')).toBeDisabled();
  });
  expect(screen.getByLabelText('Закрыть')).toBeDisabled();
  // Escape and backdrop-click route through Modal's onClose — guarded too.
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(screen.getByText('Новый канал')).toBeInTheDocument();

  resolveCreate(
    jsonResponse({
      status: 'ok',
      action_type: 'channel_create',
      account_id: 'acc-1',
      channel_id: '789',
    }),
  );
  // Settled → the dialog resolves into the editor hand-off.
  await waitFor(() => {
    expect(screen.queryByText('Новый канал')).not.toBeInTheDocument();
  });
});
