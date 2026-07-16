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

test('the save button is disabled when the first name is cleared (zod validation)', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  const firstName = screen.getByDisplayValue('Иван');
  await userEvent.clear(firstName);
  await waitFor(() => {
    expect(screen.getByText('Сохранить')).toBeDisabled();
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
