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
  reactions_enabled: true,
};

function routeApi(detail: Record<string, unknown> = DETAIL) {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/acc-1/channels/123' && request.method === 'GET') {
      return Promise.resolve(jsonResponse(detail));
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

test('the reactions checkbox reflects the live state and sends only that field', async () => {
  routeApi();
  renderWithClient(<ChannelEditModal accountId="acc-1" channelId="123" onClose={vi.fn()} />);
  await screen.findByDisplayValue('Мой канал');
  const checkbox = screen.getByRole('checkbox', { name: 'Отключить реакции' });
  // Reactions are on in the detail → the "disable" box starts unchecked.
  expect(checkbox).toHaveAttribute('aria-checked', 'false');

  await userEvent.click(checkbox);
  expect(checkbox).toHaveAttribute('aria-checked', 'true');
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(requests('/channels/123/update')).toHaveLength(1);
  });
  const body = (await (requests('/channels/123/update')[0] as Request).clone().json()) as Record<
    string,
    unknown
  >;
  expect(body).toEqual({ reactions_enabled: false });
});

test('a channel with reactions already off starts checked and can turn them back on', async () => {
  routeApi({ ...DETAIL, reactions_enabled: false });
  renderWithClient(<ChannelEditModal accountId="acc-1" channelId="123" onClose={vi.fn()} />);
  await screen.findByDisplayValue('Мой канал');
  const checkbox = screen.getByRole('checkbox', { name: 'Отключить реакции' });
  expect(checkbox).toHaveAttribute('aria-checked', 'true');
  // Back to the live state → nothing to save.
  expect(screen.getByText('Сохранить')).toBeDisabled();

  await userEvent.click(checkbox);
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(requests('/channels/123/update')).toHaveLength(1);
  });
  const body = (await (requests('/channels/123/update')[0] as Request).clone().json()) as Record<
    string,
    unknown
  >;
  expect(body).toEqual({ reactions_enabled: true });
});

test('an about-only edit stays saveable when the title prefill came back blank', async () => {
  routeApi({ ...DETAIL, title: '' });
  renderWithClient(<ChannelEditModal accountId="acc-1" channelId="123" onClose={vi.fn()} />);
  const about = await screen.findByDisplayValue('Описание канала');

  await userEvent.clear(about);
  await userEvent.type(about, 'Новое описание');

  // The blank-title guard belongs to the title alone: an unchanged title is not
  // sent at all, so a read that returned no title must not make Save dead
  // forever — and silently, the "enter a title" hint being gated on the title
  // having been touched.
  expect(screen.getByText('Сохранить')).toBeEnabled();
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

test('a blank-title read says nothing rather than announcing "Private"', async () => {
  // The no-match fallback in the backend detail read is effectively dead (see the
  // invariant on ChannelEditModal), but if it ever fired the identity line
  // confidently called a public channel private on EVERY read.
  routeApi({ ...DETAIL, title: '', username: null });
  renderWithClient(<ChannelEditModal accountId="acc-1" channelId="123" onClose={vi.fn()} />);
  await screen.findByDisplayValue('Описание канала');

  expect(screen.queryByText(/Приватный/)).not.toBeInTheDocument();
  expect(screen.queryByText(/подписчиков/)).not.toBeInTheDocument();
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

// The dialog opens BEFORE the detail read lands, and an ARIA name change while a
// dialog is open is never re-announced — so a name taken from the channel title
// would only ever be announced as "Загрузка…". A fixed name also cannot go empty,
// which `?? ` did not prevent: a blank title left the dialog nameless.
test('the dialog keeps one accessible name across the detail read', async () => {
  routeApi();
  renderWithClient(<ChannelEditModal accountId="acc-1" channelId="123" onClose={vi.fn()} />);

  expect(screen.getByRole('dialog', { name: 'Редактор канала' })).toBeInTheDocument();
  // The visible heading follows the title once it lands; the name does not. It is a real
  // heading because the fixed dialog name never carries the title, so heading navigation
  // is the only way to it.
  expect(await screen.findByDisplayValue('Мой канал')).toBeInTheDocument();
  expect(screen.getByRole('heading', { level: 2, name: 'Мой канал' })).toBeInTheDocument();
  expect(screen.getByRole('dialog', { name: 'Редактор канала' })).toBeInTheDocument();
});
