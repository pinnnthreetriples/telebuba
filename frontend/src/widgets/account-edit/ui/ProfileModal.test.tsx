import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { ProfileModal } from './ProfileModal';

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

const ACCOUNT: AccountRead = {
  account_id: 'acc-1',
  status: 'alive',
  first_name: 'Иван',
  username: 'ivanov',
  phone: '+79991234567',
  created_at: 'now',
  updated_at: 'now',
};

const VIEW = {
  error: null,
  // Live profile text matching the stored row, so auto-seeding is a no-op in
  // tests that don't exercise it explicitly.
  first_name: 'Иван',
  last_name: null,
  username: 'ivanov',
  bio: null,
  photos: [{ photo_id: 1, access_hash: 2, file_reference: 'YWJj', thumb_url: null }],
  stories: [
    {
      story_id: 3,
      kind: 'image',
      privacy_preset: 'contacts',
      is_pinned: false,
      views: 128,
      reactions: 24,
      thumb_url: null,
    },
  ],
  music: [
    { file_id: '4', title: 'Track', performer: 'Artist', access_hash: '5', file_reference: 'YWJj' },
  ],
  music_supported: true,
};

function routeApi() {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(VIEW));
    }
    if (pathname === '/api/v1/accounts/profile') {
      return Promise.resolve(jsonResponse({ ...ACCOUNT, first_name: 'Пётр' }));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
}

function fired(fragment: string, method = 'POST'): boolean {
  return vi.mocked(fetch).mock.calls.some(([input]) => {
    const request = input as Request;
    return request.url.includes(fragment) && request.method === method;
  });
}

test('edits the profile text and saves via the real endpoint', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  expect(screen.getByText('Иван')).toBeInTheDocument();

  const firstName = screen.getByDisplayValue('Иван');
  await userEvent.clear(firstName);
  await userEvent.type(firstName, 'Пётр');
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(fired('/accounts/profile')).toBe(true);
  });
});

test('photo tab uploads an avatar and removes a photo', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));

  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(fileInput, {
    target: { files: [new File(['x'], 'a.jpg', { type: 'image/jpeg' })] },
  });
  await waitFor(() => {
    expect(fired('/accounts/photo')).toBe(true);
  });

  await userEvent.click(await screen.findByLabelText('Удалить фото'));
  await userEvent.click(await screen.findByText('Удалить', { selector: 'button' }));
  await waitFor(() => {
    expect(fired('/photo/remove')).toBe(true);
  });
});

test('the photo tab bulk-uploads every picked file', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));

  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement;
  expect(fileInput.multiple).toBe(true);
  fireEvent.change(fileInput, {
    target: {
      files: [
        new File(['a'], 'a.jpg', { type: 'image/jpeg' }),
        new File(['b'], 'b.jpg', { type: 'image/jpeg' }),
        new File(['c'], 'c.jpg', { type: 'image/jpeg' }),
      ],
    },
  });
  await waitFor(() => {
    const uploads = vi
      .mocked(fetch)
      .mock.calls.filter(([input]) => (input as Request).url.includes('/accounts/photo'));
    expect(uploads).toHaveLength(3);
  });
});

test('the picker still uploads when the browser clears the live FileList on reset', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));

  // Real browsers: input.files is a live FileList and value='' EMPTIES it in
  // place. jsdom doesn't, so we emulate it — the handler must read files before
  // the reset, or this drops to zero uploads (the shipped-and-reverted bug).
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement;
  const live: File[] = [new File(['a'], 'a.jpg', { type: 'image/jpeg' })];
  Object.defineProperty(fileInput, 'files', { configurable: true, get: () => live });
  Object.defineProperty(fileInput, 'value', {
    configurable: true,
    get: () => '',
    set: () => {
      live.length = 0;
    },
  });
  fireEvent.change(fileInput);
  await waitFor(() => {
    expect(fired('/accounts/photo')).toBe(true);
  });
});

test('dropping image files on the photo tab uploads each one', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));

  const dropZone = screen.getByText('Текущая фотография и история · перетащите для замены')
    .parentElement as HTMLElement;
  fireEvent.drop(dropZone, {
    dataTransfer: {
      files: [
        new File(['a'], 'a.jpg', { type: 'image/jpeg' }),
        new File(['b'], 'b.png', { type: 'image/png' }),
      ],
    },
  });
  await waitFor(() => {
    const uploads = vi
      .mocked(fetch)
      .mock.calls.filter(([input]) => (input as Request).url.includes('/accounts/photo'));
    expect(uploads).toHaveLength(2);
  });
});

test('dropping a mix of files uploads only the images', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));

  const dropZone = screen.getByText('Текущая фотография и история · перетащите для замены')
    .parentElement as HTMLElement;
  fireEvent.drop(dropZone, {
    dataTransfer: {
      files: [
        new File(['a'], 'a.jpg', { type: 'image/jpeg' }),
        new File(['b'], 'notes.txt', { type: 'text/plain' }),
      ],
    },
  });
  await waitFor(() => {
    expect(fired('/accounts/photo')).toBe(true);
  });
  const uploads = vi
    .mocked(fetch)
    .mock.calls.filter(([input]) => (input as Request).url.includes('/accounts/photo'));
  expect(uploads).toHaveLength(1);
});

test('stories tab opens the add-story modal and removes a story', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Сторис'));

  await userEvent.click(await screen.findByLabelText('Удалить сторис'));
  await userEvent.click(await screen.findByText('Удалить', { selector: 'button' }));
  await waitFor(() => {
    expect(fired('/story/remove')).toBe(true);
  });

  await userEvent.click(screen.getByText('Добавить'));
  expect(screen.getByText('Новая сторис')).toBeInTheDocument();
});

test('stories tab shows view/reaction counts and pins a story via the endpoint', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Сторис'));

  // Engagement badges: 👁 views and ❤ reactions from the snapshot.
  expect(await screen.findByText('128')).toBeInTheDocument();
  expect(screen.getByText('24')).toBeInTheDocument();

  // An unpinned story offers "pin forever"; clicking it hits /story/pin.
  await userEvent.click(screen.getByLabelText('Закрепить в профиле навсегда'));
  await waitFor(() => {
    expect(fired('/story/pin')).toBe(true);
  });
});

test('a pinned story shows the "forever" label and offers to unpin it', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(
        jsonResponse({
          ...VIEW,
          stories: [{ story_id: 3, kind: 'image', privacy_preset: 'contacts', is_pinned: true }],
        }),
      );
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Сторис'));

  expect(await screen.findByText('📌 Навсегда')).toBeInTheDocument();
  await userEvent.click(screen.getByLabelText('Открепить — истечёт через 24 ч'));
  await waitFor(() => {
    expect(fired('/story/pin')).toBe(true);
  });
});

test('a close_friends / unknown story renders a translated label, not the raw key', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(
        jsonResponse({
          ...VIEW,
          stories: [
            { story_id: 7, kind: 'image', privacy_preset: 'close_friends', is_pinned: false },
            { story_id: 8, kind: 'image', privacy_preset: 'unknown', is_pinned: false },
          ],
        }),
      );
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Сторис'));
  expect(await screen.findByText('Близкие друзья')).toBeInTheDocument();
  expect(screen.getByText('Неизвестно')).toBeInTheDocument();
  // The raw snake-case key must never leak into the UI.
  expect(screen.queryByText('accounts.addStory.close_friends')).not.toBeInTheDocument();
});

test('the save button is disabled when the first name is cleared (zod validation)', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  const firstName = screen.getByDisplayValue('Иван');
  await userEvent.clear(firstName);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeDisabled();
  });
});

test('music tab removes the current track and picks a new one', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Музыка'));

  expect(await screen.findByText('Track')).toBeInTheDocument();
  await userEvent.click(screen.getByLabelText('Убрать трек'));
  await userEvent.click(await screen.findByText('Убрать', { selector: 'button' }));
  await waitFor(() => {
    expect(fired('/music/remove')).toBe(true);
  });

  const musicInput = document.body.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(musicInput, {
    target: { files: [new File(['x'], 't.mp3', { type: 'audio/mpeg' })] },
  });
  await waitFor(() => {
    expect(fired('/accounts/acc-1/music')).toBe(true);
  });
});

test('the refresh button force-re-pulls the live profile and updates the header', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      // A forced refresh (the «Обновить» button) re-pulls fresh live text.
      const live =
        url.searchParams.get('refresh') === 'true'
          ? { ...VIEW, first_name: 'Пётр', username: 'petr_tg' }
          : VIEW;
      return Promise.resolve(jsonResponse(live));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  // Initially the header shows the stored account row.
  expect(await screen.findByText('Иван')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Обновить'));

  const forced = () =>
    vi.mocked(fetch).mock.calls.some(([input]) => (input as Request).url.includes('refresh=true'));
  await waitFor(() => {
    expect(forced()).toBe(true);
  });
  // The header now reflects the freshly-pulled live profile.
  await waitFor(() => {
    expect(screen.getByText('Пётр')).toBeInTheDocument();
  });
});

test('clearing last name / username / bio submits empty strings (clear contract)', async () => {
  const snapshotView = { ...VIEW, last_name: 'Иванов', bio: 'старое био' };
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(snapshotView));
    }
    return Promise.resolve(jsonResponse({ ...ACCOUNT }));
  });
  renderWithClient(
    <ProfileModal
      account={{ ...ACCOUNT, last_name: 'Иванов', bio: 'старое био' }}
      onClose={vi.fn()}
    />,
  );
  await userEvent.clear(screen.getByDisplayValue('Иванов'));
  await userEvent.clear(screen.getByDisplayValue('ivanov'));
  await userEvent.clear(screen.getByDisplayValue('старое био'));
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(fired('/accounts/profile')).toBe(true);
  });
  const call = vi
    .mocked(fetch)
    .mock.calls.find(([input]) => (input as Request).url.includes('/accounts/profile'));
  const body = (await (call?.[0] as Request).clone().json()) as Record<string, unknown>;
  // '' clears the field on Telegram; null would mean "leave unchanged".
  expect(body).toMatchObject({ first_name: 'Иван', last_name: '', username: '', bio: '' });
});

test('zod enforces the Telegram limits: bio ≤70, names ≤64, username shape', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  const save = () => screen.getByText('Сохранить');
  const bio = screen.getByLabelText('Описание (bio)');

  // 71-char bio → blocked.
  fireEvent.change(bio, { target: { value: 'ю'.repeat(71) } });
  await waitFor(() => {
    expect(save()).toBeDisabled();
  });
  fireEvent.change(bio, { target: { value: 'ок' } });

  // 65-char first name → blocked.
  const firstName = screen.getByDisplayValue('Иван');
  fireEvent.change(firstName, { target: { value: 'а'.repeat(65) } });
  await waitFor(() => {
    expect(save()).toBeDisabled();
  });
  fireEvent.change(firstName, { target: { value: 'Иван' } });

  // Malformed usernames → blocked (too short / bad charset / digit-first).
  // (the field is selected by value — its wrapper label also contains the @ prefix)
  const username = screen.getByDisplayValue('ivanov');
  for (const bad of ['ab', 'иван_тг', '1ivan']) {
    fireEvent.change(username, { target: { value: bad } });
    await waitFor(() => {
      expect(save()).toBeDisabled();
    });
  }
  // Empty username is allowed — it clears the handle.
  fireEvent.change(username, { target: { value: '' } });
  await waitFor(() => {
    expect(save()).toBeEnabled();
  });
});

test('the upload tile is disabled while a photo upload is pending', async () => {
  let resolvePhoto!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(VIEW));
    }
    if (pathname === '/api/v1/accounts/photo') {
      return new Promise((resolve) => {
        resolvePhoto = resolve;
      });
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));

  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(fileInput, {
    target: { files: [new File(['x'], 'a.jpg', { type: 'image/jpeg' })] },
  });
  await waitFor(() => {
    // Mid-upload the content overlay is shown and the upload tile is disabled.
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Загрузить' })).toBeDisabled();
  });
  resolvePhoto(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  await waitFor(() => {
    // Once the batch and its background sync settle, the overlay clears.
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Загрузить' })).toBeEnabled();
  });
});

test('the remove-photo dialog stays open on failure and closes on success', async () => {
  let removeStatus = 400;
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(VIEW));
    }
    if (pathname.endsWith('/photo/remove')) {
      return removeStatus >= 400
        ? Promise.resolve(jsonResponse({ error: { code: 'bad_request', message: 'boom' } }, 400))
        : Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));
  await userEvent.click(await screen.findByLabelText('Удалить фото'));

  // Failure: the confirm dialog must stay open (the toast reports the error).
  await userEvent.click(await screen.findByText('Удалить', { selector: 'button' }));
  await waitFor(() => {
    expect(fired('/photo/remove')).toBe(true);
  });
  expect(await screen.findByText('Удалить фото?')).toBeInTheDocument();

  // Retry succeeds → the dialog closes.
  removeStatus = 200;
  await userEvent.click(screen.getByText('Удалить', { selector: 'button' }));
  await waitFor(() => {
    expect(screen.queryByText('Удалить фото?')).not.toBeInTheDocument();
  });
});

test('a pristine form re-seeds from the live snapshot when it arrives', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(
        jsonResponse({
          ...VIEW,
          first_name: 'Live',
          last_name: null,
          username: 'live_user',
          bio: 'live bio',
        }),
      );
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  // The form opens with the stored row, then syncs to the live profile.
  await waitFor(() => {
    expect(screen.getByDisplayValue('Live')).toBeInTheDocument();
  });
  expect(screen.getByDisplayValue('live_user')).toBeInTheDocument();
  expect(screen.getByDisplayValue('live bio')).toBeInTheDocument();
});

test('a late snapshot does not clobber user edits', async () => {
  let resolveSnapshot!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return new Promise((resolve) => {
        resolveSnapshot = resolve;
      });
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  const firstName = screen.getByDisplayValue('Иван');
  await userEvent.clear(firstName);
  await userEvent.type(firstName, 'Мой');

  resolveSnapshot(
    jsonResponse({ ...VIEW, first_name: 'Live', username: 'live_user', bio: 'live bio' }),
  );
  // The header reflects the snapshot, but the dirty form keeps the user's text.
  await waitFor(() => {
    expect(screen.getByText('Live')).toBeInTheDocument();
  });
  expect(screen.getByDisplayValue('Мой')).toBeInTheDocument();
  expect(screen.queryByDisplayValue('Live')).not.toBeInTheDocument();
});

test('refresh syncs the bio even when other fresh fields are null', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      const live =
        url.searchParams.get('refresh') === 'true'
          ? { ...VIEW, first_name: 'Пётр', last_name: null, username: null, bio: 'новое био' }
          : VIEW;
      return Promise.resolve(jsonResponse(live));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Обновить'));
  await waitFor(() => {
    expect(screen.getByDisplayValue('новое био')).toBeInTheDocument();
  });
  expect(screen.getByDisplayValue('Пётр')).toBeInTheDocument();
  // The username was cleared on Telegram → the field empties too.
  expect(screen.queryByDisplayValue('ivanov')).not.toBeInTheDocument();
});

test('«Сделать основным» promotes a non-main photo via the real endpoint', async () => {
  const twoPhotos = {
    ...VIEW,
    photos: [
      {
        photo_id: '111',
        access_hash: '222',
        file_reference: 'YWJj',
        thumb_url: null,
        is_main: true,
      },
      {
        photo_id: '333',
        access_hash: '444',
        file_reference: 'ZmZm',
        thumb_url: null,
        is_main: false,
      },
    ],
  };
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(twoPhotos));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));

  // The is_main photo shows the static «Основное фото» marker; only the other
  // exposes an actionable «Сделать основным» button.
  const makeMain = await screen.findByText('Сделать основным', { selector: 'button' });
  await userEvent.click(makeMain);
  await waitFor(() => {
    expect(fired('/photo/main')).toBe(true);
  });
  const call = vi
    .mocked(fetch)
    .mock.calls.find(([input]) => (input as Request).url.includes('/photo/main'));
  const body = (await (call?.[0] as Request).clone().json()) as Record<string, unknown>;
  // The int64 id is carried as a string end-to-end (no JS rounding).
  expect(body).toMatchObject({ photo_id: '333', access_hash: '444' });
});

test('the "main" marker follows is_main, not the array position', async () => {
  // The current avatar is the SECOND photo (is_main), not index 0 — the marker
  // and the actionable button must track the flag, not the order.
  const photos = {
    ...VIEW,
    photos: [
      {
        photo_id: '111',
        access_hash: '222',
        file_reference: 'YWJj',
        thumb_url: null,
        is_main: false,
      },
      {
        photo_id: '333',
        access_hash: '444',
        file_reference: 'ZmZm',
        thumb_url: null,
        is_main: true,
      },
    ],
  };
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(photos));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));

  // Exactly one static marker, one actionable button (index 0 is NOT the main).
  expect(await screen.findByText('Основное фото')).toBeInTheDocument();
  const makeMain = screen.getByText('Сделать основным', { selector: 'button' });
  await userEvent.click(makeMain);
  const call = vi
    .mocked(fetch)
    .mock.calls.find(([input]) => (input as Request).url.includes('/photo/main'));
  const body = (await (call?.[0] as Request).clone().json()) as Record<string, unknown>;
  // The promoted photo is the non-main one (id 111), proving the button is on it.
  expect(body).toMatchObject({ photo_id: '111' });
});

test('make-main refetches via the forced-refresh path (not the TTL cache)', async () => {
  const twoPhotos = {
    ...VIEW,
    photos: [
      {
        photo_id: '111',
        access_hash: '222',
        file_reference: 'YWJj',
        thumb_url: null,
        is_main: true,
      },
      {
        photo_id: '333',
        access_hash: '444',
        file_reference: 'ZmZm',
        thumb_url: null,
        is_main: false,
      },
    ],
  };
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(twoPhotos));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));
  await userEvent.click(await screen.findByText('Сделать основным', { selector: 'button' }));

  // The post-mutation refresh MUST bypass the 30s read cache — a bare invalidate
  // could return the stale photo set (the make-main duplicate/loss bug).
  await waitFor(() => {
    const forced = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('refresh=true'));
    expect(forced).toBe(true);
  });
});

test('the content overlay appears during the post-action sync and clears when it settles', async () => {
  const twoPhotos = {
    ...VIEW,
    photos: [
      {
        photo_id: '111',
        access_hash: '222',
        file_reference: 'YWJj',
        thumb_url: null,
        is_main: true,
      },
      {
        photo_id: '333',
        access_hash: '444',
        file_reference: 'ZmZm',
        thumb_url: null,
        is_main: false,
      },
    ],
  };
  let resolveRefresh!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      // Hold the forced re-pull so the in-flight overlay is observable.
      if (url.searchParams.get('refresh') === 'true') {
        return new Promise((resolve) => {
          resolveRefresh = resolve;
        });
      }
      return Promise.resolve(jsonResponse(twoPhotos));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));
  await userEvent.click(await screen.findByText('Сделать основным', { selector: 'button' }));

  // The background re-pull is in flight → the content overlay is shown.
  await waitFor(() => {
    expect(screen.getByRole('status')).toBeInTheDocument();
  });
  // Settling the pull clears the overlay.
  resolveRefresh(jsonResponse(twoPhotos));
  await waitFor(() => {
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});

test('duplicate photo_ids in the snapshot render only once', async () => {
  const dupes = {
    ...VIEW,
    photos: [
      {
        photo_id: '111',
        access_hash: '222',
        file_reference: 'YWJj',
        thumb_url: null,
        is_main: true,
      },
      {
        photo_id: '111',
        access_hash: '222',
        file_reference: 'YWJj',
        thumb_url: null,
        is_main: true,
      },
    ],
  };
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(dupes));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));

  // Both rows share photo_id 111 → the tile (and its remove button) renders once.
  expect(await screen.findAllByLabelText('Удалить фото')).toHaveLength(1);
});

test('the refresh button flashes a success state on a clean re-pull', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Обновить'));
  expect(await screen.findByText('Обновлено')).toBeInTheDocument();
});

test('the refresh button flashes an error state when the live pull fails', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      // A forced refresh that Telegram refuses returns a 200 carrying `error`.
      const live =
        url.searchParams.get('refresh') === 'true' ? { ...VIEW, error: 'floodwait' } : VIEW;
      return Promise.resolve(jsonResponse(live));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Обновить'));
  expect(await screen.findByText('Ошибка')).toBeInTheDocument();
});

test('the stories tab shows the view count on each story', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Сторис'));
  expect(await screen.findByText('128')).toBeInTheDocument();
});

test('closing with unsaved edits asks for confirmation; a clean close does not', async () => {
  routeApi();
  const onClose = vi.fn();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={onClose} />);

  // Clean close → no discard dialog.
  await userEvent.click(screen.getByLabelText('Закрыть'));
  expect(screen.queryByText('Отменить изменения?')).not.toBeInTheDocument();
  expect(onClose).toHaveBeenCalledTimes(1);

  // Dirty close → the discard dialog gates the close.
  await userEvent.type(screen.getByDisplayValue('Иван'), 'ов');
  await userEvent.click(screen.getByLabelText('Закрыть'));
  expect(screen.getByText('Отменить изменения?')).toBeInTheDocument();
  expect(onClose).toHaveBeenCalledTimes(1);
  await userEvent.click(screen.getByText('Не сохранять'));
  expect(onClose).toHaveBeenCalledTimes(2);
});

test('Save is disabled until the form is actually edited', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  const save = screen.getByText('Сохранить').closest('button');
  expect(save).toBeDisabled();
  await userEvent.type(screen.getByDisplayValue('Иван'), 'ов');
  expect(save).toBeEnabled();
});

test('a successful save clears the dirty state so closing does not prompt', async () => {
  let saved = false;
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(saved ? { ...VIEW, first_name: 'Иванов' } : VIEW));
    }
    if (pathname === '/api/v1/accounts/profile') {
      saved = true;
      return Promise.resolve(jsonResponse({ ...ACCOUNT, first_name: 'Иванов' }));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  const onClose = vi.fn();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={onClose} />);
  await userEvent.type(screen.getByDisplayValue('Иван'), 'ов');
  await userEvent.click(screen.getByText('Сохранить'));
  await waitFor(() => {
    expect(fired('/accounts/profile')).toBe(true);
  });
  await userEvent.click(screen.getByLabelText('Закрыть'));
  expect(screen.queryByText('Отменить изменения?')).not.toBeInTheDocument();
  expect(onClose).toHaveBeenCalled();
});

test('a failed snapshot load shows an error with a retry instead of empty tabs', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse({ ...VIEW, error: 'floodwait' }));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  expect(
    await screen.findByText('Не удалось загрузить данные профиля из Telegram'),
  ).toBeInTheDocument();
});

test('the music tab shows an unsupported note when Telegram lacks the TL methods', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse({ ...VIEW, music: [], music_supported: false }));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Музыка'));
  expect(
    await screen.findByText('Профильная музыка недоступна для этого аккаунта'),
  ).toBeInTheDocument();
  expect(screen.queryByText('Выбрать трек')).not.toBeInTheDocument();
});

test('the header renders the real avatar when the snapshot carries one', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(
        jsonResponse({
          ...VIEW,
          photos: [
            {
              photo_id: '1',
              access_hash: '1',
              file_reference: 'AA==',
              thumb_url: 'data:image/jpeg;base64,QQ==',
            },
          ],
        }),
      );
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await waitFor(() => {
    // The round header avatar (not a square photo tile) carries the thumbnail.
    expect(document.querySelector('.rounded-full[style*="data:image/jpeg"]')).not.toBeNull();
  });
});

const TWO_PHOTOS = {
  ...VIEW,
  photos: [
    { photo_id: '111', access_hash: '222', file_reference: 'YWJj', thumb_url: null, is_main: true },
    {
      photo_id: '333',
      access_hash: '444',
      file_reference: 'ZmZm',
      thumb_url: null,
      is_main: false,
    },
  ],
};

test('a FAILED make-main still force-refetches — the grid must drop dead photo ids', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(TWO_PHOTOS));
    }
    if (pathname.endsWith('/photo/main')) {
      // The backend consumed (replaced) the id but the request failed late —
      // the server-side snapshot cache is already invalidated.
      return Promise.resolve(
        jsonResponse({ error: { code: 'bad_request', message: 'dead' } }, 400),
      );
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));
  await userEvent.click(await screen.findByText('Сделать основным', { selector: 'button' }));

  // onSettled (not onSuccess): the failure must still trigger the forced re-pull,
  // or a retry would resend the same dead photo id forever.
  await waitFor(() => {
    const forced = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('refresh=true'));
    expect(forced).toBe(true);
  });
});

test('a FAILED photo remove still force-refetches while the dialog stays open', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(VIEW));
    }
    if (pathname.endsWith('/photo/remove')) {
      return Promise.resolve(
        jsonResponse({ error: { code: 'bad_request', message: 'boom' } }, 400),
      );
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));
  await userEvent.click(await screen.findByLabelText('Удалить фото'));
  await userEvent.click(await screen.findByText('Удалить', { selector: 'button' }));

  await waitFor(() => {
    const forced = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('refresh=true'));
    expect(forced).toBe(true);
  });
  // The rejection still propagates to ConfirmModal, so the dialog stays open.
  expect(screen.getByText('Удалить фото?')).toBeInTheDocument();
});

test('a failed post-action sync surfaces the load-error banner, not a stale grid', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      // The forced re-pull after the mutation fails outright (transport/5xx).
      if (url.searchParams.get('refresh') === 'true') {
        return Promise.resolve(jsonResponse({ error: { code: 'internal', message: 'down' } }, 500));
      }
      return Promise.resolve(jsonResponse(TWO_PHOTOS));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));
  await userEvent.click(await screen.findByText('Сделать основным', { selector: 'button' }));

  // The overlay clears AND the failure is surfaced — a silently-stale grid
  // rendered as current is exactly the F2 bug.
  expect(
    await screen.findByText('Не удалось загрузить данные профиля из Telegram'),
  ).toBeInTheDocument();
  await waitFor(() => {
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});

test('a selected_contacts story renders a translated privacy badge, not the raw key', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(
        jsonResponse({
          ...VIEW,
          stories: [
            { story_id: 9, kind: 'image', privacy_preset: 'selected_contacts', is_pinned: false },
          ],
        }),
      );
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Сторис'));
  expect(await screen.findByText('Выбранные контакты')).toBeInTheDocument();
  expect(screen.queryByText('accounts.addStory.selected_contacts')).not.toBeInTheDocument();
});

test('the photo prefilter skips files the backend would reject (suffix + size)', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));

  // A GIF (image/* MIME but a rejected suffix) and an oversized JPG must never
  // reach the wire; the valid JPG uploads alone.
  const oversized = new File(['x'], 'big.jpg', { type: 'image/jpeg' });
  Object.defineProperty(oversized, 'size', { value: 10_000_001 });
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(fileInput, {
    target: {
      files: [
        new File(['x'], 'anim.gif', { type: 'image/gif' }),
        oversized,
        new File(['x'], 'ok.jpg', { type: 'image/jpeg' }),
      ],
    },
  });
  await waitFor(() => {
    expect(fired('/accounts/photo')).toBe(true);
  });
  const uploads = vi
    .mocked(fetch)
    .mock.calls.filter(([input]) => (input as Request).url.includes('/accounts/photo'));
  expect(uploads).toHaveLength(1);
});

test('picking only rejected files uploads nothing', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));

  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(fileInput, {
    target: { files: [new File(['x'], 'photo.heic', { type: 'image/heic' })] },
  });
  // No progress overlay, no wire traffic.
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  expect(fired('/accounts/photo')).toBe(false);
});
