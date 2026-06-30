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

test('switches to the stories tab and opens the add-story modal above it', async () => {
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  // header shows the account
  expect(screen.getByText('Иван')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Сторис'));
  await userEvent.click(screen.getByText('Добавить'));
  expect(screen.getByText('Новая сторис')).toBeInTheDocument();
});

test('edits the profile text and saves via the real endpoint', async () => {
  vi.mocked(fetch).mockResolvedValue(jsonResponse({ ...ACCOUNT, first_name: 'Пётр' }));
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);

  const firstName = screen.getByDisplayValue('Иван');
  await userEvent.clear(firstName);
  await userEvent.type(firstName, 'Пётр');
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    const saved = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.endsWith('/accounts/profile'));
    expect(saved).toBe(true);
  });
});

test('uploads an avatar on the photo tab and toggles profile music', async () => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({ status: 'ok', action_type: 'set_profile_photo', account_id: 'acc-1' }),
  );
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);

  // Фото tab: the upload tile triggers the hidden file input → setAccountPhoto.
  await userEvent.click(screen.getByText('Фото'));
  await userEvent.click(screen.getByText('Загрузить'));
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement;
  const avatar = new File(['x'], 'avatar.jpg', { type: 'image/jpeg' });
  fireEvent.change(fileInput, { target: { files: [avatar] } });
  await waitFor(() => {
    const sent = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.endsWith('/accounts/photo'));
    expect(sent).toBe(true);
  });

  // Музыка tab: remove then re-pick a track (exercises the music toggles).
  await userEvent.click(screen.getByText('Музыка'));
  await userEvent.click(screen.getByLabelText('Убрать трек'));
  await userEvent.click(screen.getByText('Выбрать трек'));
});
