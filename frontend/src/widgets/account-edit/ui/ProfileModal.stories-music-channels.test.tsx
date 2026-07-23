import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { ProfileModal } from './ProfileModal';
import {
  ACCOUNT,
  VIEW,
  fired,
  jsonResponse,
  renderWithClient,
  routeApi,
} from './ProfileModal.test-helpers';

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

test('the stories tab shows the view count on each story', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Сторис'));
  expect(await screen.findByText('128')).toBeInTheDocument();
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

test('the channels tab renders its own list outside the snapshot scrim', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/acc-1/channels') {
      return Promise.resolve(
        jsonResponse({
          items: [
            { channel_id: '5', title: 'Канал профиля', username: null, participants_count: null },
          ],
          next_cursor: null,
        }),
      );
    }
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(VIEW));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Каналы'));
  expect(await screen.findByText('Канал профиля')).toBeInTheDocument();
  expect(screen.getByText('Создать канал')).toBeInTheDocument();
});
