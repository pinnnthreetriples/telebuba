import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { ProfileModal } from './ProfileModal';
import {
  ACCOUNT,
  VIEW,
  jsonResponse,
  renderWithClient,
  routeApi,
} from './ProfileModal.test-helpers';

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
