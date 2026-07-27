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

test('a failed snapshot load names WHY Telegram refused, with a retry', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      // The real shape: a 200 whose `error` carries the gateway's own label
      // (core/telegram_client/_read.py formats it), not a stable code.
      return Promise.resolve(jsonResponse({ ...VIEW, error: 'FloodWait(300s)' }));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  // "wait 5 minutes" vs "re-login this account" vs "your proxy is down" are
  // different jobs for the operator — one generic sentence for all three is a
  // banner that cannot be acted on.
  expect(
    await screen.findByText('Не удалось загрузить данные профиля из Telegram (FloodWait(300s))'),
  ).toBeInTheDocument();
});

test('the refresh button is disabled while a post-action background sync runs', async () => {
  let releaseSync!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      if (url.searchParams.get('refresh') === 'true') {
        // Hold the post-mutation forced pull so the disabled state is observable.
        return new Promise((resolve) => {
          releaseSync = resolve;
        });
      }
      return Promise.resolve(jsonResponse(VIEW));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Сторис'));
  await userEvent.click(await screen.findByLabelText('Закрепить в профиле навсегда'));

  // The background sync is in flight → «Обновить» must not start a rival pull.
  await waitFor(() => {
    expect(screen.getByText('Обновить').closest('button')).toBeDisabled();
  });
  releaseSync(jsonResponse(VIEW));
  await waitFor(() => {
    expect(screen.getByText('Обновить').closest('button')).toBeEnabled();
  });
});

test('«Обновить» stays disabled through a MANUAL pull, past the previous flash timer', async () => {
  // The post-action refresh() path (covered above) sets `syncing`; «Обновить»
  // did not, so its ONLY guard was `refreshState` — which the 1.4s ✓/✗ timer
  // clears, including one armed by an EARLIER press. Press once (fast ✓ arms the
  // timer), press again onto a slow pull, and at t≈1.45s that stale timer set
  // 'idle': the button went live while a `refresh=true` pull — which bypasses the
  // server's 30s read cache — was still running, inviting the FLOOD_WAIT this
  // file otherwise guards against.
  let forcedCalls = 0;
  let releaseSecond!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      if (url.searchParams.get('refresh') === 'true') {
        forcedCalls += 1;
        if (forcedCalls === 1) return Promise.resolve(jsonResponse(VIEW));
        return new Promise((resolve) => {
          releaseSecond = resolve;
        });
      }
      return Promise.resolve(jsonResponse(VIEW));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  const button = () => screen.getByRole('button', { name: /Обновить|Обновлено|Ошибка/ });

  await userEvent.click(screen.getByText('Обновить'));
  expect(await screen.findByText('Обновлено')).toBeInTheDocument();
  await userEvent.click(button());
  await waitFor(() => {
    expect(forcedCalls).toBe(2);
  });
  expect(button()).toBeDisabled();

  // Past the first press's 1400ms flash window, with pull #2 still in flight.
  await new Promise((resolve) => {
    setTimeout(resolve, 1600);
  });
  expect(button()).toBeDisabled();

  releaseSecond(jsonResponse(VIEW));
  await waitFor(() => {
    expect(button()).toBeEnabled();
  });
});

test('a stale forced pull cannot clobber a newer one (serialised refresh)', async () => {
  const pinned = { ...VIEW, stories: [{ ...VIEW.stories[0], is_pinned: true }] };
  let releaseStale!: (response: Response) => void;
  let forcedCalls = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      if (url.searchParams.get('refresh') === 'true') {
        forcedCalls += 1;
        if (forcedCalls === 1) {
          // The «Обновить» pull hangs and resolves LAST — with stale data.
          return new Promise((resolve) => {
            releaseStale = resolve;
          });
        }
        return Promise.resolve(jsonResponse(pinned));
      }
      return Promise.resolve(jsonResponse(VIEW));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.click(screen.getByText('Сторис'));
  // Start the slow header pull, then pin a story — its post-action refresh
  // starts a NEWER pull that lands first (the story shows as pinned).
  await userEvent.click(screen.getByText('Обновить'));
  await userEvent.click(await screen.findByLabelText('Закрепить в профиле навсегда'));
  expect(await screen.findByText('📌 Навсегда')).toBeInTheDocument();

  // The stale pull resolves last — it must NOT roll the story back to unpinned.
  releaseStale(jsonResponse(VIEW));
  await waitFor(() => {
    expect(screen.getByText('Обновить').closest('button')).toBeEnabled();
  });
  expect(screen.getByText('📌 Навсегда')).toBeInTheDocument();
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
