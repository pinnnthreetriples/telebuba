import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { NeurocommentPage } from './NeurocommentPage';

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

const CAMPAIGN = {
  campaign_id: 'c1',
  name: 'Promo',
  prompt: 'mention the product',
  status: 'active',
  created_at: 'now',
  updated_at: 'now',
  channel_count: 3,
  account_count: 5,
};

const BOARD = {
  campaign_id: 'c1',
  campaign_name: 'Promo',
  status: 'active',
  solver_enabled: true,
  channels: [{ channel: '@news', status: 'ready', ready_accounts: 1, total_accounts: 1 }],
  accounts: [
    {
      account_id: 'acc-1',
      label: '+79261112233',
      health: 'ok',
      trust_score: 80,
      trust_band: 'good',
      comments_last_hour: 0,
      max_comments_per_hour: 10,
      comments_today: 2,
      last_comment_at: 'now',
      readiness: [{ channel: '@news', ready: true, joined: true, captcha_passed: true }],
    },
  ],
};

function routeApi() {
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
}

function lastEventSource(): { emit(data: unknown): void } | undefined {
  return (
    globalThis.EventSource as unknown as { last(): { emit(d: unknown): void } | undefined }
  ).last();
}

// Variant of routeApi where the runtime already has a listener and is running,
// so the page renders the listening surface + its pause/edit/remove actions.
function routeApiRunning() {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(BOARD));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: true, active_channels: 1, listener_account_id: 'acc-1' }),
      );
    }
    if (url.pathname === '/api/v1/accounts') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              account_id: 'acc-1',
              label: '+79261112233',
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
      // acc-2 is graduated ("Прогреты") and unlinked → the idle account.
      return Promise.resolve(
        jsonResponse({ accounts: [{ account_id: 'acc-2', label: '+79261119999' }] }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });
}

test('renders campaigns and the board for the selected campaign', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  expect(screen.getByText('Готов')).toBeInTheDocument();
  expect(screen.getAllByText('Promo').length).toBeGreaterThan(0);
});

test('refetches runtime/board on a live SSE event', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  const boardCalls = () =>
    vi.mocked(fetch).mock.calls.filter(([input]) => (input as Request).url.endsWith('/board'))
      .length;
  const before = boardCalls();
  act(() => {
    lastEventSource()?.emit({ id: 1, event: 'neurocomment_comment_posted' });
  });
  await waitFor(() => {
    expect(boardCalls()).toBeGreaterThan(before);
  });
});

test('the create-campaign button opens the create modal', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  await userEvent.click(screen.getByText('+ Создать кампанию'));
  expect(screen.getByText('Создать кампанию')).toBeInTheDocument();
});

test('the gear in the board header opens the accounts modal', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  await userEvent.click(screen.getByLabelText('Аккаунты в нейрокомментинге'));
  expect(screen.getByText('Готово')).toBeInTheDocument();
});

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
      // acc-2 is graduated ("Прогреты") and unlinked → the idle account.
      return Promise.resolve(
        jsonResponse({ accounts: [{ account_id: 'acc-2', label: '+79261119999' }] }),
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

test('picking a listener account enables the start button', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  // Listener is a custom dropdown; open it and choose the account.
  await userEvent.click(screen.getByText('Выберите аккаунт…'));
  const option = await screen.findByRole('button', { name: '+79261112233' });
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
  expect(await screen.findByRole('button', { name: '+79261112233' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '+79261119999' })).not.toBeInTheDocument();
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
  await userEvent.click(await screen.findByRole('button', { name: '+79261112233' }));
  await userEvent.click(screen.getByText('Запустить'));
  // The swallowed 409 is now reflected as the localized warming banner.
  await waitFor(() => {
    expect(screen.getByText(/нельзя назначить слушателем/)).toBeInTheDocument();
  });
});

test('selecting a campaign card marks it selected', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('Promo').length).toBeGreaterThan(0);
  });
  const card = screen
    .getAllByText('Promo')
    .map((node) => node.closest('[role="button"]'))
    .find((node): node is HTMLElement => node !== null);
  expect(card).toBeDefined();
  await userEvent.click(card as HTMLElement);
  expect((card as HTMLElement).className).toContain('border-primary');
  // sanity: status pill uses the active campaign-status key path
  within(card as HTMLElement).getByText('Активна');
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

test('toggling the captcha solver persists the campaign override', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  const sw = screen.getByRole('switch', { name: 'Решение капчи' });
  expect(sw).toHaveAttribute('aria-checked', 'true');
  await userEvent.click(sw);
  await waitFor(() => {
    const posted = vi.mocked(fetch).mock.calls.some(([input]) => {
      const request = input as Request;
      return request.url.includes('/campaigns/c1/solver') && request.method === 'POST';
    });
    expect(posted).toBe(true);
  });
});

test('the captcha solver toggle reflects the persisted value after a real round trip', async () => {
  // Unlike routeApi() (a static mock), this simulates a real backend: the POST
  // actually updates the value the next GET /board returns.
  let solverEnabled = true;
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/solver') && request.method === 'POST') {
      return request
        .clone()
        .json()
        .then((body: { enabled: boolean }) => {
          solverEnabled = body.enabled;
          return new Response(null, { status: 204 });
        });
    }
    if (url.pathname.endsWith('/board')) {
      return Promise.resolve(jsonResponse({ ...BOARD, solver_enabled: solverEnabled }));
    }
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: null }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });

  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  const sw = screen.getByRole('switch', { name: 'Решение капчи' });
  expect(sw).toHaveAttribute('aria-checked', 'true');

  await userEvent.click(sw);
  await waitFor(() => {
    expect(sw).toHaveAttribute('aria-checked', 'false');
  });

  await userEvent.click(sw);
  await waitFor(() => {
    expect(sw).toHaveAttribute('aria-checked', 'true');
  });
});

test('Решить retries a challenged pair', async () => {
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
    if (url.pathname.endsWith('/challenges')) {
      return Promise.resolve(
        jsonResponse({
          rows: [
            {
              account_id: 'acc-9',
              channel: '@x',
              raw_text: 'cap',
              outcome: 'failed',
              decided_at: '2026-06-30T12:00:00+00:00',
            },
          ],
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getByText('Пройти')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Пройти'));
  await waitFor(() => {
    const retried = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.endsWith('/neurocomment/retry'));
    expect(retried).toBe(true);
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

test('campaign edit-prompt saves and delete removes the campaign', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('Promo').length).toBeGreaterThan(0);
  });

  await userEvent.click(screen.getByTitle('Редактировать промт'));
  // Bug fix: an unpinned account shows the CAMPAIGN scope in the modal, not an
  // arbitrary first-readiness channel (`@news`). The account subtitle is the only
  // muted-text 'Promo' on the page.
  expect(await screen.findByText('Promo', { selector: '.text-ink-muted' })).toBeInTheDocument();
  await userEvent.click(await screen.findByText('Сохранить'));
  await waitFor(() => {
    const saved = vi.mocked(fetch).mock.calls.some(([input]) => {
      const request = input as Request;
      return request.url.includes('/campaigns/c1/prompt') && request.method === 'PUT';
    });
    expect(saved).toBe(true);
  });

  await userEvent.click(screen.getByTitle('Удалить кампанию'));
  await userEvent.click(await screen.findByText('Удалить'));
  await waitFor(() => {
    const deleted = vi.mocked(fetch).mock.calls.some(([input]) => {
      const request = input as Request;
      return request.url.endsWith('/neurocomment/campaigns/c1') && request.method === 'DELETE';
    });
    expect(deleted).toBe(true);
  });
});

test('removing a campaign channel asks for confirmation, then calls the deactivate endpoint', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  await userEvent.click(screen.getByLabelText('Убрать канал'));
  const removeConfirm = await screen.findByText('Убрать');
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/channels/remove')),
  ).toBe(false);
  await userEvent.click(removeConfirm);
  await waitFor(() => {
    const removed = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/channels/remove'));
    expect(removed).toBe(true);
  });
});

test('the add-channel pill reveals an input and adds the channel', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });

  await userEvent.click(screen.getByText('+ Канал'));
  const input = screen.getByPlaceholderText(/Введите|@|канал/i);
  await userEvent.type(input, '@promo');
  // The add button shares its aria-label with the modal's add ("Добавить").
  await userEvent.click(screen.getByRole('button', { name: 'Добавить' }));
  await waitFor(() => {
    const linked = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/channels'));
    expect(linked).toBe(true);
  });
});

test('the create-campaign modal closes via cancel', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  await userEvent.click(screen.getByText('+ Создать кампанию'));
  expect(screen.getByText('Создать кампанию')).toBeInTheDocument();
  await userEvent.click(screen.getAllByText('Отмена')[0]!);
  await waitFor(() => {
    expect(screen.queryByText('Создать кампанию')).not.toBeInTheDocument();
  });
});

test('a campaign card shows its OWN channel/account counts, not the board totals', async () => {
  // Finding #3: counts come from the campaign payload (3 / 5), not the board.
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('Promo').length).toBeGreaterThan(0);
  });
  expect(screen.getByText('3 каналов · 5 аккаунтов')).toBeInTheDocument();
});

test('per-campaign run/pause calls setCampaignStatus, not the global stop', async () => {
  // Finding #2: an active campaign's pause button flips its status via the
  // status endpoint; it must NOT hit /neurocomment/stop.
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('Promo').length).toBeGreaterThan(0);
  });
  // The active campaign card exposes a "pause" action (title from campaign.status).
  await userEvent.click(screen.getAllByTitle('Поставить на паузу')[0]!);
  await waitFor(() => {
    const setStatus = vi.mocked(fetch).mock.calls.some(([input]) => {
      const request = input as Request;
      return request.url.endsWith('/campaigns/c1/status') && request.method === 'POST';
    });
    expect(setStatus).toBe(true);
  });
  const stopped = vi
    .mocked(fetch)
    .mock.calls.some(([input]) => (input as Request).url.endsWith('/neurocomment/stop'));
  expect(stopped).toBe(false);
});

test('the campaign gear toggles the slide-out actions', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('Promo').length).toBeGreaterThan(0);
  });
  const gear = screen.getAllByLabelText('Действия')[0]!;
  expect(gear).toHaveAttribute('aria-expanded', 'false');
  await userEvent.click(gear);
  expect(gear).toHaveAttribute('aria-expanded', 'true');
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

test('the pipeline stats include the errors odometer', async () => {
  routeApi();
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  expect(screen.getByText('ошибок')).toBeInTheDocument();
});

test('the captcha queue shows the account phone, not the raw id', async () => {
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
              status: 'alive',
              created_at: 'n',
              updated_at: 'n',
            },
          ],
          next_cursor: null,
        }),
      );
    }
    if (url.pathname.endsWith('/challenges')) {
      return Promise.resolve(
        jsonResponse({
          rows: [
            {
              account_id: 'acc-1',
              channel: '@x',
              raw_text: 'cap',
              outcome: 'failed',
              decided_at: '2026-06-30T12:00:00+00:00',
            },
          ],
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getByText('Пройти')).toBeInTheDocument();
  });
  // Phone from the accounts list, not the raw "acc-1" id.
  expect(screen.getAllByText('+79261112233').length).toBeGreaterThan(0);
  expect(screen.queryByText('acc-1')).not.toBeInTheDocument();
});

test('the neuro log localizes a known event code and falls back for an unknown one', async () => {
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
    if (url.pathname === '/api/v1/logs') {
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              id: 1,
              created_at: 'now',
              level: 'INFO',
              status: 'success',
              event: 'neurocomment_posted',
            },
            {
              id: 2,
              created_at: 'now',
              level: 'INFO',
              status: 'success',
              event: 'some_unmapped_code',
            },
          ],
          next_cursor: null,
        }),
      );
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getByText('Комментарий опубликован')).toBeInTheDocument();
  });
  // Unmapped code renders verbatim.
  expect(screen.getByText('some_unmapped_code')).toBeInTheDocument();
});

test('the clear-log trash confirms, then DELETEs only the neurocomment logs', async () => {
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
    if (url.pathname === '/api/v1/logs' && request.method === 'GET') {
      return Promise.resolve(
        jsonResponse({
          items: [{ id: 1, created_at: 'now', level: 'INFO', status: 'success', event: 'x' }],
          next_cursor: null,
        }),
      );
    }
    if (url.pathname === '/api/v1/logs' && request.method === 'DELETE') {
      return Promise.resolve(jsonResponse({ deleted: 1 }));
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getByLabelText('Очистить лог')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByLabelText('Очистить лог'));
  const confirm = await screen.findByText('Очистить');
  const wasDeleted = () =>
    vi.mocked(fetch).mock.calls.some(([input]) => {
      const request = input as Request;
      const url = new URL(request.url);
      return (
        request.method === 'DELETE' &&
        url.pathname === '/api/v1/logs' &&
        url.searchParams.get('event_prefix') === 'neurocomment'
      );
    });
  expect(wasDeleted()).toBe(false); // not until confirmed
  await userEvent.click(confirm);
  await waitFor(() => {
    expect(wasDeleted()).toBe(true);
  });
});

test('the SSE callback invalidates only this page keys, not the whole cache', async () => {
  routeApi();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(queryClient, 'invalidateQueries');
  render(<QueryClientProvider client={queryClient}>{<NeurocommentPage />}</QueryClientProvider>);
  await waitFor(() => {
    expect(screen.getAllByText('@news').length).toBeGreaterThan(0);
  });
  spy.mockClear();
  act(() => {
    lastEventSource()?.emit({ id: 1, event: 'neurocomment_posted', status: 'success' });
  });
  await waitFor(() => {
    expect(spy).toHaveBeenCalled();
  });
  // Every SSE-driven invalidation is scoped by a predicate (not a bare call).
  expect(
    spy.mock.calls.every(([arg]) => typeof arg === 'object' && arg !== null && 'predicate' in arg),
  ).toBe(true);
});

test('checking channels colours banned chips red and healthy chips green', async () => {
  const board2 = {
    ...BOARD,
    channels: [
      { channel: '@news', status: 'ready', ready_accounts: 1, total_accounts: 1 },
      { channel: '@promo', status: 'ready', ready_accounts: 1, total_accounts: 1 },
    ],
  };
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname.endsWith('/channel-bans') && request.method === 'POST') {
      return Promise.resolve(
        jsonResponse({
          items: [
            { channel: '@news', status: 'banned' },
            { channel: '@promo', status: 'ok' },
          ],
        }),
      );
    }
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) return Promise.resolve(jsonResponse(board2));
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: null }),
      );
    }
    if (url.pathname === '/api/v1/accounts') {
      return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
    }
    return Promise.resolve(jsonResponse({}));
  });

  const chip = (channel: string): HTMLElement | null =>
    screen
      .getAllByLabelText('Убрать канал')
      .map((btn) => btn.closest('span'))
      .find((span) => span?.textContent?.includes(channel)) ?? null;

  renderWithClient(<NeurocommentPage />);
  await waitFor(() => {
    expect(screen.getByText('Проверить каналы')).toBeInTheDocument();
  });
  await waitFor(() => {
    expect(chip('@news')).not.toBeNull();
  });

  await userEvent.click(screen.getByText('Проверить каналы'));

  await waitFor(() => {
    expect(chip('@news')?.className).toContain('text-danger');
  });
  expect(chip('@promo')?.className).toContain('text-[#2e9e64]');
});
