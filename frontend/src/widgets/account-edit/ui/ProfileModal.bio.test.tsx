import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { ProfileModal } from './ProfileModal';
import { ACCOUNT, VIEW, fired, jsonResponse, renderWithClient } from './ProfileModal.test-helpers';

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

test('a refused bio keeps the typed text in the field, not the old one from Telegram', async () => {
  routeBioPulls(['старое био']);
  renderWithClient(<ProfileModal account={{ ...ACCOUNT, bio: 'старое био' }} onClose={vi.fn()} />);

  await saveNewBio();
  await screen.findByTestId('bio-not-applied');

  // Re-seeding from the live pull here would hand back the refused text.
  expect(screen.getByDisplayValue('мой канал @somewhere')).toBeInTheDocument();
  expect(screen.queryByDisplayValue('старое био')).not.toBeInTheDocument();
});

test('a later save dirtied by another field re-sends the bio, not the refused one', async () => {
  const view = { ...VIEW, last_name: 'Иванов', bio: 'старое био' };
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse(view));
    }
    return Promise.resolve(jsonResponse({ ...ACCOUNT, last_name: 'Иванов', bio: 'старое био' }));
  });
  renderWithClient(
    <ProfileModal
      account={{ ...ACCOUNT, last_name: 'Иванов', bio: 'старое био' }}
      onClose={vi.fn()}
    />,
  );

  await saveNewBio();
  await screen.findByTestId('bio-not-applied');

  const lastName = screen.getByDisplayValue('Иванов');
  await userEvent.clear(lastName);
  await userEvent.type(lastName, 'Петров');
  // The footer shows «Сохранено» for 1.4s after the first save.
  await waitFor(() => expect(screen.getByText('Сохранить')).toBeEnabled(), { timeout: 3000 });
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    const saves = vi
      .mocked(fetch)
      .mock.calls.filter(([input]) => (input as Request).url.includes('/accounts/profile'));
    expect(saves.length).toBe(2);
  });
  const saves = vi
    .mocked(fetch)
    .mock.calls.filter(([input]) => (input as Request).url.includes('/accounts/profile'));
  const body = (await (saves.at(-1)?.[0] as Request).clone().json()) as Record<string, unknown>;
  expect(body).toMatchObject({ last_name: 'Петров', bio: 'мой канал @somewhere' });
});

test('a refused «Обновить» marks the snapshot untrustworthy instead of just flashing', async () => {
  let failForced = false;
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      if (url.searchParams.get('refresh') === 'true' && failForced) {
        return Promise.reject(new Error('network down'));
      }
      return Promise.resolve(jsonResponse({ ...VIEW, bio: 'старое био' }));
    }
    return Promise.resolve(jsonResponse({ ...ACCOUNT, bio: 'старое био' }));
  });
  renderWithClient(<ProfileModal account={{ ...ACCOUNT, bio: 'старое био' }} onClose={vi.fn()} />);

  await saveNewBio();
  await screen.findByTestId('bio-not-applied');

  // The message tells the operator to press «Обновить»; if that read is refused
  // the verdict must go silent rather than be recomputed from stale fields.
  failForced = true;
  await userEvent.click(screen.getByText('Обновить'));

  await waitFor(() => {
    expect(screen.getByText('Не удалось загрузить данные профиля из Telegram')).toBeInTheDocument();
  });
  expect(screen.queryByTestId('bio-not-applied')).not.toBeInTheDocument();
});

// The post-save pull differing in ANY unrelated field (a story view count, a
// rotated file_reference) makes react-query hand back a new object, so the
// re-seed effect actually runs — the case the identical-payload fixtures above
// cannot reach.
function routeBioPullsWithDrift(forcedBio: string) {
  let pull = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      if (url.searchParams.get('refresh') === 'true') {
        pull += 1;
        return Promise.resolve(
          jsonResponse({
            ...VIEW,
            bio: forcedBio,
            stories: VIEW.stories.map((story) => ({ ...story, views: 128 + pull })),
          }),
        );
      }
      return Promise.resolve(jsonResponse({ ...VIEW, bio: 'старое био' }));
    }
    return Promise.resolve(jsonResponse({ ...ACCOUNT, bio: 'старое био' }));
  });
}

test('a re-seed carrying unrelated drift still keeps the refused bio in the field', async () => {
  routeBioPullsWithDrift('старое био');
  renderWithClient(<ProfileModal account={{ ...ACCOUNT, bio: 'старое био' }} onClose={vi.fn()} />);

  await saveNewBio();
  await screen.findByTestId('bio-not-applied');

  expect(screen.getByDisplayValue('мой канал @somewhere')).toBeInTheDocument();
});

test('after drift re-seeds, a save dirtied elsewhere still sends the typed bio', async () => {
  routeBioPullsWithDrift('старое био');
  renderWithClient(
    <ProfileModal
      account={{ ...ACCOUNT, last_name: 'Иванов', bio: 'старое био' }}
      onClose={vi.fn()}
    />,
  );

  await saveNewBio();
  await screen.findByTestId('bio-not-applied');

  const firstName = screen.getByDisplayValue('Иван');
  await userEvent.clear(firstName);
  await userEvent.type(firstName, 'Пётр');
  await waitFor(() => expect(screen.getByText('Сохранить')).toBeEnabled(), { timeout: 3000 });
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    const saves = vi
      .mocked(fetch)
      .mock.calls.filter(([input]) => (input as Request).url.includes('/accounts/profile'));
    expect(saves.length).toBe(2);
  });
  const saves = vi
    .mocked(fetch)
    .mock.calls.filter(([input]) => (input as Request).url.includes('/accounts/profile'));
  const body = (await (saves.at(-1)?.[0] as Request).clone().json()) as Record<string, unknown>;
  expect(body).toMatchObject({ first_name: 'Пётр', bio: 'мой канал @somewhere' });
});

test('a saved name is not restored to its pre-save value by the form baseline', async () => {
  // The baseline must move for EVERY field, not just the bio. With no drift the
  // re-seed effect does not re-run, so the baseline is the only thing deciding
  // what the field holds — and it must hold what was saved.
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname === '/api/v1/accounts/acc-1/profile-snapshot') {
      return Promise.resolve(jsonResponse({ ...VIEW, first_name: 'Иван' }));
    }
    return Promise.resolve(jsonResponse({ ...ACCOUNT, first_name: 'Пётр' }));
  });
  renderWithClient(<ProfileModal account={ACCOUNT} onClose={vi.fn()} />);

  const firstName = screen.getByDisplayValue('Иван');
  await userEvent.clear(firstName);
  await userEvent.type(firstName, 'Пётр');
  await userEvent.click(screen.getByText('Сохранить'));

  await waitFor(() => {
    expect(fired('/accounts/profile')).toBe(true);
  });
  await new Promise((resolve) => {
    setTimeout(resolve, 100);
  });
  expect(screen.getByDisplayValue('Пётр')).toBeInTheDocument();
});

test('editing the bio releases it back to «Обновить», which re-seeds from Telegram', async () => {
  // `onRefresh` seeds unconditionally — it is not gated on the form being clean —
  // so the verdict the guard consults must be cleared by the edit, or a pressed
  // «Обновить» would silently leave the bio behind while the other fields sync.
  routeBioPullsWithDrift('старое био');
  renderWithClient(<ProfileModal account={{ ...ACCOUNT, bio: 'старое био' }} onClose={vi.fn()} />);

  await saveNewBio();
  await screen.findByTestId('bio-not-applied');

  await userEvent.type(screen.getByTestId('profile-bio'), 'X');
  await userEvent.click(screen.getByText('Обновить'));

  await waitFor(() => {
    expect(screen.getByDisplayValue('старое био')).toBeInTheDocument();
  });
});
