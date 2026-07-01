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

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

const ACCOUNT: AccountRead = {
  account_id: 'acc-1',
  status: 'alive',
  first_name: 'Иван',
  username: 'ivan',
  phone: '+79991234567',
  created_at: 'now',
  updated_at: 'now',
};

const VIEW = {
  error: null,
  avatar_data_uri: null,
  photos: [{ photo_id: 1, access_hash: 2, file_reference: 'YWJj', thumb_data_uri: null }],
  stories: [
    {
      story_id: 3,
      kind: 'image',
      privacy_preset: 'contacts',
      is_pinned: false,
      thumb_data_uri: null,
    },
  ],
  music: [
    { file_id: 4, title: 'Track', performer: 'Artist', access_hash: 5, file_reference: 'YWJj' },
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
  await waitFor(() => {
    expect(fired('/photo/remove')).toBe(true);
  });
});

test('stories tab opens the add-story modal and removes a story', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Сторис'));

  await userEvent.click(await screen.findByLabelText('Удалить сторис'));
  await waitFor(() => {
    expect(fired('/story/remove')).toBe(true);
  });

  await userEvent.click(screen.getByText('Добавить'));
  expect(screen.getByText('Новая сторис')).toBeInTheDocument();
});

test('music tab removes the current track and picks a new one', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Музыка'));

  expect(await screen.findByText('Track')).toBeInTheDocument();
  await userEvent.click(screen.getByLabelText('Убрать трек'));
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
