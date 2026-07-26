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

test('a rejected save shows the translated username error under the field', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(VIEW));
    }
    if (pathname === '/api/v1/accounts/profile') {
      // The stable code rides the envelope's message (backend contract).
      return Promise.resolve(
        jsonResponse({ error: { code: 'bad_request', message: 'username_occupied' } }, 409),
      );
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.type(screen.getByDisplayValue('ivanov'), '2');
  await userEvent.click(screen.getByText('Сохранить'));
  expect(await screen.findByText('Юзернейм уже занят')).toBeInTheDocument();
});

test('a flood_wait rejection shows the retry-after seconds on any tab', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(VIEW));
    }
    if (pathname === '/api/v1/accounts/profile') {
      // Real backend contract: envelope fields serialise as STRINGS
      // (api/errors.py; tests/api/test_accounts.py) and a flood ride is a 400.
      return Promise.resolve(
        jsonResponse(
          {
            error: {
              code: 'bad_request',
              message: 'flood_wait',
              fields: { retry_after_seconds: '345' },
            },
          },
          400,
        ),
      );
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.type(screen.getByDisplayValue('Иван'), 'ов');
  await userEvent.click(screen.getByText('Сохранить'));
  expect(
    await screen.findByText('Telegram ограничил действия — повторите через 345 с'),
  ).toBeInTheDocument();
  // Save (and its error) is global footer state — it must survive a tab switch.
  await userEvent.click(screen.getByText('Фото'));
  expect(
    screen.getByText('Telegram ограничил действия — повторите через 345 с'),
  ).toBeInTheDocument();
});

test('the inline server error clears as soon as the field is edited', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(VIEW));
    }
    if (pathname === '/api/v1/accounts/profile') {
      return Promise.resolve(
        jsonResponse({ error: { code: 'bad_request', message: 'username_occupied' } }, 409),
      );
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.type(screen.getByDisplayValue('ivanov'), '2');
  await userEvent.click(screen.getByText('Сохранить'));
  expect(await screen.findByText('Юзернейм уже занят')).toBeInTheDocument();

  // Editing the field again invalidates the stale server verdict.
  await userEvent.type(screen.getByDisplayValue('ivanov2'), '3');
  await waitFor(() => {
    expect(screen.queryByText('Юзернейм уже занят')).not.toBeInTheDocument();
  });
});

test('an account with no stored first name shows why Save is disabled', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse({ ...VIEW, first_name: null, username: null }));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(
    <ProfileModal account={{ ...ACCOUNT, first_name: null, username: null }} onClose={vi.fn()} />,
  );
  // The required-name reason renders without the field ever being touched.
  expect(await screen.findByText('Укажите имя')).toBeInTheDocument();
  expect(screen.getByText('Сохранить').closest('button')).toBeDisabled();
});

test('seeding a name over the empty stored one lets a later edit save', async () => {
  // The case the test above deliberately does not cover: the stored row has no
  // first name, so onMount flags it, but the live snapshot DOES carry one. Seeding
  // has to refresh the verdict it just disproved. Left stale it read «Укажите имя»
  // under a field showing the real name, and — the part that actually traps the
  // operator — Save stayed dead through any subsequent edit, because canSubmit
  // counts onMount errors and editing one field never clears another's.
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse({ ...VIEW, first_name: 'Иван' }));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={{ ...ACCOUNT, first_name: null }} onClose={vi.fn()} />);

  expect(await screen.findByDisplayValue('Иван')).toBeInTheDocument();
  await waitFor(() => {
    expect(screen.queryByText('Укажите имя')).not.toBeInTheDocument();
  });

  // Save is still disabled here only because nothing is dirty yet; editing an
  // unrelated field is what has to bring it back.
  await userEvent.type(screen.getByLabelText('Фамилия'), 'Иванов');
  await waitFor(() => {
    expect(screen.getByText('Сохранить').closest('button')).toBeEnabled();
  });
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

// Telegram can answer updateProfile with ok and silently ignore `about`, so the
// save response proves nothing: the post-save live pull (refresh=true) is the
// only witness. These tests drive that pull, one entry per forced read.
function routeBioPulls(forcedBios: string[]) {
  let pull = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      const forced = url.searchParams.get('refresh') === 'true';
      const bio = forced ? (forcedBios[pull++] ?? forcedBios.at(-1)) : 'старое био';
      return Promise.resolve(jsonResponse({ ...VIEW, bio }));
    }
    if (url.pathname === '/api/v1/accounts/profile') {
      return Promise.resolve(jsonResponse({ ...ACCOUNT, bio: 'старое био' }));
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
}

async function saveNewBio() {
  const bio = screen.getByDisplayValue('старое био');
  await userEvent.clear(bio);
  await userEvent.type(bio, 'мой канал @somewhere');
  await userEvent.click(screen.getByText('Сохранить'));
}

test('warns under the bio field when the live pull still reports the old bio', async () => {
  routeBioPulls(['старое био']);
  renderWithClient(<ProfileModal account={{ ...ACCOUNT, bio: 'старое био' }} onClose={vi.fn()} />);

  await saveNewBio();

  expect(await screen.findByTestId('bio-not-applied')).toBeInTheDocument();
});

test('no bio warning when the live pull confirms the submitted bio', async () => {
  routeBioPulls(['мой канал @somewhere']);
  renderWithClient(<ProfileModal account={{ ...ACCOUNT, bio: 'старое био' }} onClose={vi.fn()} />);

  await saveNewBio();

  await waitFor(() => {
    expect(fired('/accounts/profile')).toBe(true);
  });
  expect(screen.queryByTestId('bio-not-applied')).not.toBeInTheDocument();
});

test('a later pull reporting the new bio clears the warning (replication lag)', async () => {
  // First forced pull lags behind the write, the second one catches up: the
  // warning must not survive as a permanent false alarm.
  routeBioPulls(['старое био', 'мой канал @somewhere']);
  renderWithClient(<ProfileModal account={{ ...ACCOUNT, bio: 'старое био' }} onClose={vi.fn()} />);

  await saveNewBio();
  await screen.findByTestId('bio-not-applied');

  await userEvent.click(screen.getByText('Обновить'));

  await waitFor(() => {
    expect(screen.queryByTestId('bio-not-applied')).not.toBeInTheDocument();
  });
});

test('a failed live pull shows no bio warning (a refused read is not evidence)', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return url.searchParams.get('refresh') === 'true'
        ? Promise.reject(new Error('network down'))
        : Promise.resolve(jsonResponse({ ...VIEW, bio: 'старое био' }));
    }
    return Promise.resolve(jsonResponse({ ...ACCOUNT, bio: 'старое био' }));
  });
  renderWithClient(<ProfileModal account={{ ...ACCOUNT, bio: 'старое био' }} onClose={vi.fn()} />);

  await saveNewBio();

  await waitFor(() => {
    expect(fired('/accounts/profile')).toBe(true);
  });
  expect(screen.queryByTestId('bio-not-applied')).not.toBeInTheDocument();
});

test('no bio warning flashes while the post-save pull is still in flight', async () => {
  // Until the forced pull lands, the rendered snapshot is still the pre-save
  // one — reading that as "Telegram dropped it" would flash a false warning on
  // every successful bio edit.
  let resolveForced!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      if (url.searchParams.get('refresh') === 'true') {
        return new Promise((resolve) => {
          resolveForced = resolve;
        });
      }
      return Promise.resolve(jsonResponse({ ...VIEW, bio: 'старое био' }));
    }
    return Promise.resolve(jsonResponse({ ...ACCOUNT, bio: 'старое био' }));
  });
  renderWithClient(<ProfileModal account={{ ...ACCOUNT, bio: 'старое био' }} onClose={vi.fn()} />);

  await saveNewBio();
  await waitFor(() => {
    expect(fired('/accounts/profile')).toBe(true);
  });
  expect(screen.queryByTestId('bio-not-applied')).not.toBeInTheDocument();

  resolveForced(jsonResponse({ ...VIEW, bio: 'мой канал @somewhere' }));

  await waitFor(() => {
    expect(screen.getByDisplayValue('мой канал @somewhere')).toBeInTheDocument();
  });
  expect(screen.queryByTestId('bio-not-applied')).not.toBeInTheDocument();
});

test('a late plain read cannot resurrect the pre-save bio or its warning', async () => {
  // The mount-time cacheable read shares the snapshot key with the forced pull.
  // Resolving last it would put the pre-save bio back — reverting the field and
  // warning about a bio that did land — and nothing re-pulls afterwards.
  let resolvePlain!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      if (url.searchParams.get('refresh') === 'true') {
        return Promise.resolve(jsonResponse({ ...VIEW, bio: 'мой канал @somewhere' }));
      }
      return new Promise((resolve) => {
        resolvePlain = resolve;
      });
    }
    return Promise.resolve(jsonResponse({ ...ACCOUNT, bio: 'старое био' }));
  });
  renderWithClient(<ProfileModal account={{ ...ACCOUNT, bio: 'старое био' }} onClose={vi.fn()} />);

  await saveNewBio();
  await waitFor(() => {
    expect(screen.getByDisplayValue('мой канал @somewhere')).toBeInTheDocument();
  });

  resolvePlain(jsonResponse({ ...VIEW, bio: 'старое био' }));
  // Give the cancelled read every chance to land before asserting it did not.
  await new Promise((resolve) => {
    setTimeout(resolve, 100);
  });

  expect(screen.queryByTestId('bio-not-applied')).not.toBeInTheDocument();
  expect(screen.getByDisplayValue('мой канал @somewhere')).toBeInTheDocument();
});

test('clearing the bio warns when Telegram keeps the old text', async () => {
  routeBioPulls(['старое био']);
  renderWithClient(<ProfileModal account={{ ...ACCOUNT, bio: 'старое био' }} onClose={vi.fn()} />);

  await userEvent.clear(screen.getByDisplayValue('старое био'));
  await userEvent.click(screen.getByText('Сохранить'));

  expect(await screen.findByTestId('bio-not-applied')).toBeInTheDocument();
});

test('a bio typed with surrounding spaces is compared as it was sent (trimmed)', async () => {
  // The body carries value.bio.trim(); comparing the untrimmed field against it
  // would warn on every bio the operator happens to type with a stray space.
  routeBioPulls(['мой канал @somewhere']);
  renderWithClient(<ProfileModal account={{ ...ACCOUNT, bio: 'старое био' }} onClose={vi.fn()} />);

  const bio = screen.getByDisplayValue('старое био');
  await userEvent.clear(bio);
  await userEvent.type(bio, '  мой канал @somewhere  ');
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(fired('/accounts/profile')).toBe(true);
  });
  expect(screen.queryByTestId('bio-not-applied')).not.toBeInTheDocument();
});

test('editing the bio again clears a stale not-applied warning', async () => {
  routeBioPulls(['старое био']);
  renderWithClient(<ProfileModal account={{ ...ACCOUNT, bio: 'старое био' }} onClose={vi.fn()} />);

  await saveNewBio();
  await screen.findByTestId('bio-not-applied');

  await userEvent.type(screen.getByTestId('profile-bio'), 'x');

  expect(screen.queryByTestId('bio-not-applied')).not.toBeInTheDocument();
});
