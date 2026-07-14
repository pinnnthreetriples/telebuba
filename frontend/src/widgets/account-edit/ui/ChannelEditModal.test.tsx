import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { Toaster } from '@/shared/ui';

import { PHOTO_MAX_BYTES } from './_channelsShared';
import { ChannelEditModal } from './ChannelEditModal';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      {ui}
      <Toaster />
    </QueryClientProvider>,
  );
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const DETAIL = {
  channel_id: '123',
  title: 'Мой канал',
  username: 'mychan',
  participants_count: 42,
  about: 'Описание канала',
};

function routeApi() {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/acc-1/channels/123' && request.method === 'GET') {
      return Promise.resolve(jsonResponse(DETAIL));
    }
    if (pathname === '/api/v1/accounts/acc-1/channels/123/posts' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
}

function requests(fragment: string, method = 'POST'): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter(
      (request) => new URL(request.url).pathname.endsWith(fragment) && request.method === method,
    );
}

// The avatar input is the first file input (the posts panel's attach is second).
function avatarInput(): HTMLInputElement {
  return document.body.querySelector('input[type="file"]') as HTMLInputElement;
}

test('renders the live detail and disables save while unchanged', async () => {
  routeApi();
  renderWithClient(<ChannelEditModal accountId="acc-1" channelId="123" onClose={vi.fn()} />);
  expect(await screen.findByDisplayValue('Мой канал')).toBeInTheDocument();
  expect(screen.getByDisplayValue('Описание канала')).toBeInTheDocument();
  // The header line combines "@username · N subscribers" in one node.
  expect(screen.getByText(/@mychan/)).toBeInTheDocument();
  expect(screen.getByText('Сохранить')).toBeDisabled();
});

test('saving a retitled channel sends ONLY the changed field', async () => {
  routeApi();
  renderWithClient(<ChannelEditModal accountId="acc-1" channelId="123" onClose={vi.fn()} />);
  const title = await screen.findByDisplayValue('Мой канал');

  await userEvent.clear(title);
  await userEvent.type(title, 'Новое имя');
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(requests('/channels/123/update')).toHaveLength(1);
  });
  const body = (await (requests('/channels/123/update')[0] as Request).clone().json()) as Record<
    string,
    unknown
  >;
  // Unchanged fields are omitted — the backend treats absent as "leave as is".
  expect(body).toEqual({ title: 'Новое имя' });
  // Settled → detail + list re-pull (initial detail GET + refetch).
  await waitFor(() => {
    expect(requests('/channels/123', 'GET').length).toBeGreaterThanOrEqual(2);
  });
});

test('editing only the about sends only the about', async () => {
  routeApi();
  renderWithClient(<ChannelEditModal accountId="acc-1" channelId="123" onClose={vi.fn()} />);
  const about = await screen.findByDisplayValue('Описание канала');

  await userEvent.clear(about);
  await userEvent.type(about, 'Новое описание');
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(requests('/channels/123/update')).toHaveLength(1);
  });
  const body = (await (requests('/channels/123/update')[0] as Request).clone().json()) as Record<
    string,
    unknown
  >;
  expect(body).toEqual({ about: 'Новое описание' });
});

test('an oversized or wrong-type avatar is rejected client-side with a toast', async () => {
  routeApi();
  renderWithClient(<ChannelEditModal accountId="acc-1" channelId="123" onClose={vi.fn()} />);
  await screen.findByDisplayValue('Мой канал');

  const oversized = new File(['x'], 'big.jpg', { type: 'image/jpeg' });
  Object.defineProperty(oversized, 'size', { value: PHOTO_MAX_BYTES + 1 });
  fireEvent.change(avatarInput(), { target: { files: [oversized] } });
  expect(await screen.findByText(/«big\.jpg» пропущен/)).toBeInTheDocument();

  const gif = new File(['x'], 'anim.gif', { type: 'image/gif' });
  fireEvent.change(avatarInput(), { target: { files: [gif] } });
  expect(await screen.findByText(/«anim\.gif» пропущен/)).toBeInTheDocument();

  // Neither file reached the endpoint.
  expect(requests('/channels/123/photo')).toHaveLength(0);
});

test('a valid avatar uploads as multipart and refreshes the detail', async () => {
  routeApi();
  renderWithClient(<ChannelEditModal accountId="acc-1" channelId="123" onClose={vi.fn()} />);
  await screen.findByDisplayValue('Мой канал');

  fireEvent.change(avatarInput(), {
    target: { files: [new File(['x'], 'ava.png', { type: 'image/png' })] },
  });

  await waitFor(() => {
    expect(requests('/channels/123/photo')).toHaveLength(1);
  });
  const form = await (requests('/channels/123/photo')[0] as Request).clone().formData();
  expect((form.get('file') as File).name).toBe('ava.png');
  await waitFor(() => {
    expect(requests('/channels/123', 'GET').length).toBeGreaterThanOrEqual(2);
  });
});

test('closing with unsaved edits asks for confirmation first', async () => {
  routeApi();
  const onClose = vi.fn();
  renderWithClient(<ChannelEditModal accountId="acc-1" channelId="123" onClose={onClose} />);
  const title = await screen.findByDisplayValue('Мой канал');

  await userEvent.type(title, ' 2');
  await userEvent.click(screen.getByLabelText('Закрыть'));
  expect(onClose).not.toHaveBeenCalled();
  expect(await screen.findByText('Отменить изменения?')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Не сохранять'));
  expect(onClose).toHaveBeenCalled();
});

test('a failed detail load shows the translated reason and retry recovers', async () => {
  let failing = true;
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/acc-1/channels/123' && request.method === 'GET') {
      return Promise.resolve(
        failing
          ? jsonResponse({ error: { code: 'bad_request', message: 'channel_not_found' } }, 400)
          : jsonResponse(DETAIL),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderWithClient(<ChannelEditModal accountId="acc-1" channelId="123" onClose={vi.fn()} />);
  expect(await screen.findByText('Канал не найден')).toBeInTheDocument();

  failing = false;
  await userEvent.click(screen.getByText('Повторить'));
  expect(await screen.findByDisplayValue('Мой канал')).toBeInTheDocument();
});
