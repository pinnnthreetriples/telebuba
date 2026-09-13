import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';
import { toastError } from '@/shared/ui';

import { BulkEditModal } from './BulkEditModal';

// The modal has no <Toaster/> of its own, so the queue is what we assert on.
vi.mock('@/shared/ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/ui')>()),
  toastError: vi.fn(),
}));

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

type Sent = { url: string; fileName: string | null };

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

// Multipart bodies are read here, while the Request is still unconsumed: what
// each account actually received is the whole question for the media tabs.
function routeApi(): Sent[] {
  const sent: Sent[] = [];
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts' && request.method === 'GET') {
      return jsonResponse({ items: FLEET, next_cursor: null });
    }
    if (request.method === 'POST') {
      let fileName: string | null = null;
      try {
        const form = await request.clone().formData();
        const file = form.get('file') ?? form.get('files');
        fileName = file instanceof File ? file.name : null;
      } catch {
        // not multipart (the avatar re-sync has no body at all)
      }
      sent.push({ url: pathname, fileName });
    }
    return jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' });
  });
  return sent;
}

function renderModal() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <BulkEditModal account={ACC1} onClose={vi.fn()} />
    </QueryClientProvider>,
  );
}

const photo = (name: string) => new File(['x'], name, { type: 'image/jpeg' });

// The dashed tile is a button; the input it clicks is hidden, so the pick is
// driven on the input itself — the same way the account-edit import tests do it.
function pick(files: File[]) {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (!input) throw new Error('no file input');
  fireEvent.change(input, { target: { files } });
}

async function selectWholeFleet(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Добавить аккаунты' }));
  await screen.findByRole('checkbox', { name: 'Выбрать все (3)' });
  await user.click(screen.getByRole('checkbox', { name: 'Выбрать все (3)' }));
  await user.click(screen.getByRole('button', { name: 'Добавить (3)' }));
}

test('one photo for all uploads the same file to every account, then re-syncs each avatar', async () => {
  const sent = routeApi();
  const user = userEvent.setup();
  renderModal();

  await selectWholeFleet(user);
  await user.click(screen.getByRole('tab', { name: 'Фото' }));
  pick([photo('one.jpg')]);

  await user.click(screen.getByRole('button', { name: 'Применить к 3 аккаунтам' }));
  await waitFor(() => {
    expect(screen.getByText('Применено 3 из 3')).toBeInTheDocument();
  });

  const uploads = sent.filter((row) => row.url === '/api/v1/accounts/photo');
  expect(uploads).toHaveLength(3);
  expect(uploads.every((row) => row.fileName === 'one.jpg')).toBe(true);
  expect(sent.filter((row) => row.url.endsWith('/avatar/resync'))).toHaveLength(3);
});

test('one each hands out the set in order and cycles it when the batch is longer', async () => {
  const sent = routeApi();
  const user = userEvent.setup();
  renderModal();

  await selectWholeFleet(user);
  await user.click(screen.getByRole('tab', { name: 'Фото' }));
  await user.click(screen.getByRole('radio', { name: 'Раздать по одному' }));
  pick([photo('a.jpg'), photo('b.jpg')]);

  await user.click(screen.getByRole('button', { name: 'Применить к 3 аккаунтам' }));
  await waitFor(() => {
    expect(screen.getByText('Применено 3 из 3')).toBeInTheDocument();
  });

  expect(
    sent.filter((row) => row.url === '/api/v1/accounts/photo').map((row) => row.fileName),
  ).toEqual(['a.jpg', 'b.jpg', 'a.jpg']);
});

test('a story goes out from every account, a track likewise', async () => {
  const sent = routeApi();
  const user = userEvent.setup();
  renderModal();

  await selectWholeFleet(user);
  await user.click(screen.getByRole('tab', { name: 'Сторис' }));
  pick([photo('story.jpg')]);
  await user.click(screen.getByRole('button', { name: 'Применить к 3 аккаунтам' }));
  await waitFor(() => {
    expect(screen.getByText('Применено 3 из 3')).toBeInTheDocument();
  });

  expect(sent.filter((row) => row.url.endsWith('/story'))).toHaveLength(3);
  expect(sent.map((row) => row.url).every((url) => !url.endsWith('/music'))).toBe(true);
});

test('nothing to apply keeps the button disabled', async () => {
  routeApi();
  const user = userEvent.setup();
  renderModal();

  await user.click(screen.getByRole('tab', { name: 'Музыка' }));
  expect(screen.getByRole('button', { name: 'Применить к 1 аккаунту' })).toBeDisabled();
  expect(screen.getByText('1 трек')).toBeInTheDocument();
});

// The backend takes .mp3/.m4a only (`_PROFILE_MUSIC_SUFFIXES`); the picker used to
// advertise `audio/*` and turn one bad pick into one 400 per account.
test('a track the backend would refuse never reaches the batch', async () => {
  const sent = routeApi();
  const user = userEvent.setup();
  renderModal();

  await user.click(screen.getByRole('tab', { name: 'Музыка' }));
  pick([new File(['x'], 'track.flac', { type: 'audio/flac' })]);

  await waitFor(() => {
    expect(vi.mocked(toastError)).toHaveBeenCalledWith(
      '«track.flac» пропущен — .mp3, .m4a до 30 МБ',
    );
  });
  expect(screen.getByRole('button', { name: 'Применить к 1 аккаунту' })).toBeDisabled();
  expect(sent.filter((row) => row.url.endsWith('/music'))).toHaveLength(0);
});

test('an oversized avatar is refused before a single upload', async () => {
  const sent = routeApi();
  const user = userEvent.setup();
  renderModal();

  await user.click(screen.getByRole('tab', { name: 'Фото' }));
  const big = new File([new Uint8Array(11_000_000)], 'huge.jpg', { type: 'image/jpeg' });
  pick([big]);

  await waitFor(() => {
    expect(vi.mocked(toastError)).toHaveBeenCalledWith(
      '«huge.jpg» пропущен — .jpg, .jpeg, .png, .webp до 10 МБ',
    );
  });
  expect(screen.getByRole('button', { name: 'Применить к 1 аккаунту' })).toBeDisabled();
  expect(sent.filter((row) => row.url === '/api/v1/accounts/photo')).toHaveLength(0);
});
