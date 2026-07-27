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

test('a programmatic re-seed does NOT clear an unaddressed save error', async () => {
  // The inverse of the test above, and the one that traps the operator: the save
  // is refused, the footer/field says why, and pressing «Обновить» — which the
  // message itself invites, to check the account — re-seeds the text fields.
  // `seedField` writes through setFieldValue, which builds a new `values` object
  // even for an identical value, so the store subscription read it as "the
  // operator addressed this" and erased the verdict with nothing fixed. Worse
  // here than for flood_wait: the username is re-seeded to Telegram's current
  // one, so the handle changes under the operator AND the reason disappears.
  let pull = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      if (url.searchParams.get('refresh') === 'true') {
        pull += 1;
        // Unrelated drift (a view count) so react-query hands back a NEW object
        // and the re-seed effect actually runs — an identical payload is
        // structurally shared and would not exercise this at all.
        return Promise.resolve(
          jsonResponse({
            ...VIEW,
            username: 'ivanov_real',
            stories: VIEW.stories.map((story) => ({ ...story, views: 128 + pull })),
          }),
        );
      }
      return Promise.resolve(jsonResponse(VIEW));
    }
    if (url.pathname === '/api/v1/accounts/profile') {
      return Promise.resolve(
        jsonResponse({ error: { code: 'bad_request', message: 'username_occupied' } }, 400),
      );
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.type(screen.getByDisplayValue('ivanov'), '2');
  await userEvent.click(screen.getByText('Сохранить'));
  expect(await screen.findByText('Юзернейм уже занят')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Обновить'));
  // The re-seed landed…
  await waitFor(() => {
    expect(screen.getByDisplayValue('ivanov_real')).toBeInTheDocument();
  });
  // …and the verdict is still on screen.
  expect(screen.getByText('Юзернейм уже занят')).toBeInTheDocument();
});

test('a save refused by a gateway outage reads as words, not the bare code', async () => {
  // `unavailable` is emitted for every pool/socket failure on every editing
  // action but has copy only under accounts.channel.code — a single-table lookup
  // printed the raw identifier inline next to a correctly-worded toast.
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(VIEW));
    }
    if (pathname === '/api/v1/accounts/profile') {
      return Promise.resolve(
        jsonResponse({ error: { code: 'unavailable', message: 'unavailable' } }, 503),
      );
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.type(screen.getByDisplayValue('Иван'), 'ов');
  await userEvent.click(screen.getByText('Сохранить'));

  // …and it is announced: a rejected save was rendered with no live region at
  // all, while the busy scrim beside it already had one.
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Telegram временно недоступен — попробуйте ещё раз');
  expect(screen.queryByText('unavailable')).not.toBeInTheDocument();
});

test('a 422 points at the field the server rejected, not at the whole form', async () => {
  // api/errors.py answers validation failures with message="validation_error"
  // and the per-field reason in fields["body.<name>"]. Reading only the message
  // put a whole-form sentence beside Save while the offending field looked fine —
  // reachable whenever zod and Pydantic disagree (bio counted trimmed here,
  // untrimmed there).
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(VIEW));
    }
    if (pathname === '/api/v1/accounts/profile') {
      return Promise.resolve(
        jsonResponse(
          {
            error: {
              code: 'validation_error',
              message: 'validation_error',
              fields: { 'body.bio': 'String should have at most 70 characters' },
            },
          },
          422,
        ),
      );
    }
    return Promise.resolve(jsonResponse({ status: 'ok', action_type: 'x', account_id: 'acc-1' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  await userEvent.type(screen.getByTestId('profile-bio'), 'био');
  await userEvent.click(screen.getByText('Сохранить'));

  const message = await screen.findByText('Запрос отклонён — проверьте выделенные поля');
  // Under the bio field (its own label), not in the footer.
  expect(message.closest('label')).toContainElement(screen.getByTestId('profile-bio'));
});

test('the tab header is a real tablist with the shown tab marked', async () => {
  // Six bare <button>s: the active tab was conveyed by colour and a border only,
  // so nothing announced which of them is showing.
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);
  expect(screen.getAllByRole('tab')).toHaveLength(6);
  expect(screen.getByRole('tab', { selected: true })).toHaveTextContent('Текст');

  await userEvent.click(screen.getByRole('tab', { name: 'Фото' }));
  expect(screen.getByRole('tab', { selected: true })).toHaveTextContent('Фото');
  expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'profile-tab-photo');
});

test('the tablist is one tab stop and the arrow keys move between the tabs', async () => {
  routeApi();
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);

  // Roving tabindex: only the selected tab is reachable with Tab, so the six tabs
  // are one stop in the page order instead of six.
  const tabs = screen.getAllByRole('tab');
  expect(tabs.filter((tab) => tab.getAttribute('tabindex') === '0')).toHaveLength(1);
  expect(screen.getByRole('tab', { selected: true })).toHaveAttribute('tabindex', '0');

  const tablist = screen.getByRole('tab', { name: 'Текст' });
  tablist.focus();
  await userEvent.keyboard('{ArrowRight}');
  expect(screen.getByRole('tab', { selected: true })).toHaveTextContent('Фото');
  expect(screen.getByRole('tab', { name: 'Фото' })).toHaveFocus();

  // Wraps backwards off the first tab, and Home/End jump to the ends.
  await userEvent.keyboard('{ArrowLeft}{ArrowLeft}');
  expect(screen.getByRole('tab', { selected: true })).toHaveTextContent('Приватность');
  await userEvent.keyboard('{Home}');
  expect(screen.getByRole('tab', { selected: true })).toHaveTextContent('Текст');
  await userEvent.keyboard('{End}');
  expect(screen.getByRole('tab', { selected: true })).toHaveTextContent('Приватность');
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
