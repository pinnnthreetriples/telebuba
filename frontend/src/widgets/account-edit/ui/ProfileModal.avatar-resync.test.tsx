import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { ProfileModal } from './ProfileModal';
import { ACCOUNT, VIEW, fired, jsonResponse, renderWithClient } from './ProfileModal.test-helpers';

// /accounts/photo dropped its per-file avatar re-sync (18 wasted RPCs on a
// 10-photo batch, every one but the last immediately superseded) and exposed the
// re-sync as its own endpoint the client calls ONCE after the batch. These cover
// the client half: without it the accounts-table avatar stayed stale until the
// next session check.
const SNAPSHOT = '/api/v1/accounts/acc-1/profile-snapshot';
const UPLOAD = '/api/v1/accounts/photo';
const RESYNC = '/api/v1/accounts/acc-1/avatar/resync';

function paths(): string[] {
  return vi.mocked(fetch).mock.calls.map(([input]) => new URL((input as Request).url).pathname);
}

function pickThree(): void {
  fireEvent.change(document.body.querySelector('input[type="file"]') as HTMLInputElement, {
    target: {
      files: [
        new File(['a'], 'a.jpg', { type: 'image/jpeg' }),
        new File(['b'], 'b.jpg', { type: 'image/jpeg' }),
        new File(['c'], 'c.jpg', { type: 'image/jpeg' }),
      ],
    },
  });
}

test('a photo batch re-syncs the list avatar exactly ONCE, after the last upload', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === SNAPSHOT) return Promise.resolve(jsonResponse(VIEW));
    if (pathname === RESYNC) {
      return Promise.resolve(jsonResponse({ ...ACCOUNT, avatar_etag: 'fresh' }));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));
  pickThree();

  // Exactly one — per file is the whole point of the endpoint's existence.
  await waitFor(() => {
    expect(paths().filter((path) => path === RESYNC)).toHaveLength(1);
  });
  const seen = paths();
  expect(seen.filter((path) => path === UPLOAD)).toHaveLength(3);
  // ...and it follows the LAST upload, so it re-syncs the avatar the batch
  // actually left behind rather than one that is about to be superseded.
  expect(seen.lastIndexOf(UPLOAD)).toBeLessThan(seen.indexOf(RESYNC));
});

test('a single-file pick re-syncs too — it runs the same batch loop', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === SNAPSHOT) return Promise.resolve(jsonResponse(VIEW));
    if (pathname === RESYNC) return Promise.resolve(jsonResponse(ACCOUNT));
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));
  fireEvent.change(document.body.querySelector('input[type="file"]') as HTMLInputElement, {
    target: { files: [new File(['a'], 'a.jpg', { type: 'image/jpeg' })] },
  });

  await waitFor(() => {
    expect(paths().filter((path) => path === RESYNC)).toHaveLength(1);
  });
});

test('a refused avatar re-sync does not break the batch or surface an error', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === SNAPSHOT) return Promise.resolve(jsonResponse(VIEW));
    if (url.pathname === RESYNC) {
      return Promise.resolve(
        jsonResponse({ error: { code: 'not_found', message: 'unknown account' } }, 404),
      );
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));
  pickThree();

  await waitFor(() => {
    expect(fired('/avatar/resync')).toBe(true);
  });
  // The rejection is swallowed, so the batch's success path continues: the forced
  // snapshot re-pull still runs. The avatar is cosmetic — a refused re-sync must
  // not read as a failed upload, so there is no error banner and the tab unlocks.
  await waitFor(() => {
    const forced = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('refresh=true'));
    expect(forced).toBe(true);
  });
  await waitFor(() => {
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
  expect(screen.getByRole('button', { name: 'Загрузить' })).toBeEnabled();
  expect(screen.queryByText(/Не удалось загрузить данные профиля/)).not.toBeInTheDocument();
});

test('remove-photo and set-main do NOT call the re-sync — theirs is server-side', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === SNAPSHOT) return Promise.resolve(jsonResponse(VIEW));
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Фото'));

  await userEvent.click(await screen.findByLabelText('Удалить фото'));
  await userEvent.click(await screen.findByText('Удалить', { selector: 'button' }));
  await waitFor(() => {
    expect(fired('/photo/remove')).toBe(true);
  });
  // services/accounts/media.py re-syncs inside remove_account_profile_photo and
  // set_account_main_profile_photo: one click, one refresh, nothing superseded.
  // A client-side call here would just double the RPC bill.
  expect(fired('/avatar/resync')).toBe(false);
});
