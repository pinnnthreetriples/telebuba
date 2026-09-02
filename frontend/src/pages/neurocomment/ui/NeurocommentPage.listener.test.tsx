import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import {
  BOARD,
  CAMPAIGN,
  jsonResponse,
  renderWithClient,
  routeApi,
  routeApiRunning,
} from './NeurocommentPage.testHelpers';
import { NeurocommentPage } from './NeurocommentPage';

test('an idle account (loaded but not linked) can be assigned to the campaign', async () => {
  // acc-1 is on the board (linked); acc-2 is loaded but NOT linked → idle. The
  // modal must surface the idle account with an "assign" button (finding #1).
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: null }),
      );
    }
    if (url.pathname === '/api/v1/accounts') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              account_id: 'acc-1',
              label: '+79261112233',
              phone: '+79261112233',
              status: 'alive',
              created_at: 'n',
              updated_at: 'n',
            },
            {
              account_id: 'acc-2',
              label: '+79261119999',
              status: 'alive',
              created_at: 'n',
              updated_at: 'n',
            },
          ],
          next_cursor: null,
        }),
      );
    }
    if (url.pathname === '/api/v1/warming/warmed') {
      // acc-2 is graduated + handed off to NC and unlinked → the idle account.
      return Promise.resolve(
        jsonResponse({
          accounts: [{ account_id: 'acc-2', label: '+79261119999', nc_handed_off: true }],
        }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  await userEvent.click(screen.getByLabelText('Аккаунты в нейрокомментинге'));
  // The idle account row shows an assign button (only idle accounts get one).
  const assign = screen.getByText('Добавить в кампанию');
  expect(assign).toBeInTheDocument();
  await userEvent.click(assign);
  await waitFor(() => {
    const assigned = vi
      .mocked(fetch)
      .mock.calls.some(
        ([i]) =>
          (i as Request).url.endsWith('/campaigns/c1/accounts') && (i as Request).method === 'POST',
      );
    expect(assigned).toBe(true);
  });
});

test('a graduated account NOT handed off does not count as idle here', async () => {
  // acc-2 is warmed/graduated but nc_handed_off:false → it still lives on the
  // warming page's warmed card, not in the neurocomment idle pool.
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: null }),
      );
    }
    if (url.pathname === '/api/v1/warming/warmed') {
      return Promise.resolve(
        jsonResponse({
          accounts: [{ account_id: 'acc-2', label: '+79261119999', nc_handed_off: false }],
        }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  // No idle banner: the only warmed account has not been handed off.
  expect(screen.queryByText(/простаива/)).not.toBeInTheDocument();
});

test('picking a listener account enables the start button', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  // Listener is a custom dropdown; open it and choose the account.
  await userEvent.click(screen.getByText('Выберите аккаунт…'));
  const option = await screen.findByRole('option', { name: '+79261112233' });
  await userEvent.click(option);
  // Start button uses the existing runtime.start key ("Запустить").
  await userEvent.click(screen.getByText('Запустить'));
  await waitFor(() => {
    const started = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.endsWith('/neurocomment/start'));
    expect(started).toBe(true);
  });
});

test('an actively-warming account is not offered as a listener', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    // The warming board shares the "/board" suffix with the neurocomment board,
    // so match it first: acc-2 is actively warming and must be excluded.
    if (url.pathname === '/api/v1/warming/board') {
      return Promise.resolve(
        jsonResponse({
          idle: [],
          warming: [{ account_id: 'acc-2', label: '+79261119999', state: 'active', health: 'ok' }],
          channels: { channels: [] },
        }),
      );
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: null }),
      );
    }
    if (url.pathname === '/api/v1/accounts') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              account_id: 'acc-1',
              label: '+79261112233',
              phone: '+79261112233',
              status: 'alive',
              created_at: 'n',
              updated_at: 'n',
            },
            {
              account_id: 'acc-2',
              label: '+79261119999',
              status: 'alive',
              created_at: 'n',
              updated_at: 'n',
            },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  await userEvent.click(screen.getByText('Выберите аккаунт…'));
  // acc-1 is offered; the actively-warming acc-2 is filtered out of the picker.
  expect(await screen.findByRole('option', { name: '+79261112233' })).toBeInTheDocument();
  expect(screen.queryByRole('option', { name: '+79261119999' })).not.toBeInTheDocument();
});

test('a persisted listener that is warming disables Start under the banner', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname === '/api/v1/warming/board') {
      return Promise.resolve(
        jsonResponse({
          idle: [],
          warming: [{ account_id: 'acc-2', label: '+79261119999', state: 'active', health: 'ok' }],
          channels: { channels: [] },
        }),
      );
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      // The stale pick: acc-2 was saved as listener before it went into warming.
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: 'acc-2' }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });
  renderWithClient(<NeurocommentPage />);
  expect(
    await screen.findByText('Этот аккаунт сейчас в прогреве — его нельзя назначить слушателем'),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Запустить' })).toBeDisabled();
});

test('surfaces the backend 409 when a picked listener turns out to be warming', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    // Client sees no warming accounts (stale board), so the pre-check passes and
    // the start actually fires — exercising the 409 path.
    if (url.pathname === '/api/v1/warming/board') {
      return Promise.resolve(jsonResponse({ idle: [], warming: [], channels: { channels: [] } }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: null }),
      );
    }
    if (url.pathname === '/api/v1/neurocomment/start') {
      return Promise.resolve(
        new Response(JSON.stringify({ error: { code: 'conflict', message: 'warming' } }), {
          status: 409,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    }
    if (url.pathname === '/api/v1/accounts') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              account_id: 'acc-1',
              label: '+79261112233',
              phone: '+79261112233',
              status: 'alive',
              created_at: 'n',
              updated_at: 'n',
            },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  await userEvent.click(screen.getByText('Выберите аккаунт…'));
  await userEvent.click(await screen.findByRole('option', { name: '+79261112233' }));
  await userEvent.click(screen.getByText('Запустить'));
  // The swallowed 409 is now reflected as the localized warming banner.
  await waitFor(() => {
    expect(screen.getByText(/нельзя назначить слушателем/)).toBeInTheDocument();
  });
});

test('listener pause/edit/remove actions fire their handlers', async () => {
  routeApiRunning();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });

  // The running listener surface shows a pause action (toggleRuntime → stop).
  // Both the listener and the active campaign expose a "pause" title; the
  // listener's is first in the DOM.
  await userEvent.click(screen.getAllByTitle('Поставить на паузу')[0]!);
  await waitFor(() => {
    const stopped = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.endsWith('/neurocomment/stop'));
    expect(stopped).toBe(true);
  });

  // Edit opens the listener-edit modal; close it to exercise both handlers.
  await userEvent.click(screen.getByTitle('Изменить аккаунт'));
  await userEvent.click(screen.getByText('Отмена'));

  // Remove is distinct from pause: it clears the listener via the dedicated
  // clear endpoint (finding #4), not /neurocomment/stop.
  await userEvent.click(screen.getByTitle('Снять слушателя'));
  await waitFor(() => {
    const cleared = vi
      .mocked(fetch)
      .mock.calls.some(([input]) =>
        (input as Request).url.endsWith('/neurocomment/listener/clear'),
      );
    expect(cleared).toBe(true);
  });
});

test('the idle-accounts banner opens the accounts modal', async () => {
  routeApiRunning();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  // Two accounts, one linked → one idle → the banner renders.
  await userEvent.click(screen.getByText(/простаивающих/));
  expect(screen.getByText('Готово')).toBeInTheDocument();
  await userEvent.click(screen.getByText('Готово'));
});

test('the listener strip names channels the listener could not resolve', async () => {
  // A channel missing from the listener's filter still renders `ready` on the board, so
  // the strip is the only place the operator can see that its posts never arrive.
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({
          running: true,
          active_channels: 1,
          unwatched_channels: ['@YSMSPRZ65'],
          listener_account_id: 'acc-1',
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderWithClient(<NeurocommentPage />);

  expect(await screen.findByText(/@YSMSPRZ65/)).toBeInTheDocument();
  expect(screen.getByText(/Не слушаются/)).toBeInTheDocument();
});

test('no unwatched-channel warning when the whole watch set is live', async () => {
  // routeApiRunning returns no unwatched_channels → the strip stays clean.
  routeApiRunning();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });

  expect(screen.queryByText(/Не слушаются/)).not.toBeInTheDocument();
});

test('after pausing, the listener strip still shows the remembered account', async () => {
  // Finding #4: runtime returns listener_account_id even when running is false,
  // so the strip shows the paused listener rather than the "choose" dropdown.
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: 'acc-1' }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getByText('На паузе')).toBeInTheDocument();
  });
  // Not the empty "choose an account" affordance.
  expect(screen.queryByText('Выберите аккаунт…')).not.toBeInTheDocument();
});

test('saving a new listener while the engine is stopped persists the pick', async () => {
  // The regression: the modal only kept the pick in local state (and only pushed it to
  // the backend as a side effect of Start), so a stopped engine forgot it and the
  // persisted listener came back on reload.
  // The pointer the backend holds, so the card is read back the way the operator reads it.
  let persisted = 'acc-1';
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/neurocomment/listener' && request.method === 'POST') {
      persisted = 'acc-2';
      return Promise.resolve(jsonResponse({ running: false, listener_account_id: persisted }));
    }
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: persisted }),
      );
    }
    if (url.pathname === '/api/v1/accounts') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              account_id: 'acc-1',
              label: 'acc-1',
              first_name: 'Alisa',
              status: 'alive',
              created_at: 'n',
              updated_at: 'n',
            },
            {
              account_id: 'acc-2',
              label: 'acc-2',
              first_name: 'Polina',
              status: 'alive',
              created_at: 'n',
              updated_at: 'n',
            },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });

  await userEvent.click(screen.getByTitle('Изменить аккаунт'));
  const modal = screen.getByRole('dialog', { name: 'Аккаунт-слушатель' });
  // The name shows twice inside the modal: on the closed trigger and in its option list.
  await userEvent.click(within(modal).getAllByText('Alisa')[0]!);
  await userEvent.click(within(modal).getByText('Polina'));
  await userEvent.click(within(modal).getByText('Сохранить'));

  await waitFor(() => {
    const saved = vi
      .mocked(fetch)
      .mock.calls.find(
        ([input]) =>
          (input as Request).url.endsWith('/api/v1/neurocomment/listener') &&
          (input as Request).method === 'POST',
      );
    expect(saved).toBeDefined();
  });
  const saved = vi
    .mocked(fetch)
    .mock.calls.find(
      ([input]) =>
        (input as Request).url.endsWith('/api/v1/neurocomment/listener') &&
        (input as Request).method === 'POST',
    )!;
  // The account that was picked, not the one that was already there.
  expect(await (saved[0] as Request).clone().json()).toEqual({ listener_account_id: 'acc-2' });
  // Remembering is not starting: the stopped engine stays stopped.
  const started = vi
    .mocked(fetch)
    .mock.calls.some(([input]) => (input as Request).url.endsWith('/neurocomment/start'));
  expect(started).toBe(false);
  // The operator's own check (step 5 of the report): reopen the modal and read back
  // which account is selected. It used to be the old one.
  await waitFor(
    () => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    },
    { timeout: 2000 },
  );
  await userEvent.click(screen.getByTitle('Изменить аккаунт'));
  const reopened = screen.getByRole('dialog', { name: 'Аккаунт-слушатель' });
  // Twice for the selected account (the closed trigger plus its option), once for the
  // one that is only an option — so the trigger names Polina, not Alisa.
  expect(within(reopened).getAllByText('Polina').length).toBe(2);
  expect(within(reopened).getAllByText('Alisa').length).toBe(1);
});
