import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { ProfileModal } from './ProfileModal';
import {
  ACCOUNT,
  VIEW,
  TWO_PHOTOS,
  fired,
  jsonResponse,
  renderWithClient,
  routeApi,
} from './ProfileModal.test-helpers';

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
