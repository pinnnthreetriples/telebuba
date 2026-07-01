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

test('assigning an unpaired board account calls the assign endpoint and shows feedback', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/neurocomment/campaigns' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ campaigns: [CAMPAIGN] }));
    }
    if (url.pathname.endsWith('/board')) {
      return Promise.resolve(
        jsonResponse({ ...BOARD, accounts: [{ ...BOARD.accounts[0]!, readiness: [] }] }),
      );
    }
    if (url.pathname === '/api/v1/neurocomment/runtime') {
      return Promise.resolve(
        jsonResponse({ running: false, active_channels: 0, listener_account_id: null }),
      );
    }
    if (url.pathname === '/api/v1/accounts') {
      return Promise.resolve(
        jsonResponse({
          items: [
            { account_id: 'acc-1', label: '+79261112233', status: 'alive', created_at: 'n', updated_at: 'n' },
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
  await userEvent.click(screen.getByLabelText('Аккаунты в нейрокомментинге'));
  await userEvent.click(screen.getByText('Добавить в кампанию'));
  await waitFor(() => {
    const assigned = vi
      .mocked(fetch)
      .mock.calls.some(
        ([i]) => (i as Request).url.endsWith('/campaigns/c1/accounts') && (i as Request).method === 'POST',
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

  // Remove clears the local listener and stops the runtime again.
  const stopCalls = () =>
    vi
      .mocked(fetch)
      .mock.calls.filter(([input]) => (input as Request).url.endsWith('/neurocomment/stop')).length;
  const before = stopCalls();
  await userEvent.click(screen.getByTitle('Снять слушателя'));
  await waitFor(() => {
    expect(stopCalls()).toBeGreaterThan(before);
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
    vi.mocked(fetch).mock.calls.some(([input]) => (input as Request).url.includes('/channels/remove')),
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
