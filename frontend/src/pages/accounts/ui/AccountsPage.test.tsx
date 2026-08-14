import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead } from '@/shared/api';

import { AccountsPage } from './AccountsPage';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function account(id: string): AccountRead {
  return { account_id: id, status: 'alive', created_at: 'now', updated_at: 'now' };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

// Route the mocked fetch by path/method so list + stats + actions + pagination resolve.
function routeApi(options: {
  page1: unknown;
  page2?: unknown;
  listStatus?: number;
  checkStatus?: number;
  stats?: unknown;
}) {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/accounts/stats' && request.method === 'GET') {
      return Promise.resolve(
        jsonResponse(options.stats ?? { total: 0, active: 0, idle: 0, needs_code: 0, problem: 0 }),
      );
    }
    if (url.pathname === '/api/v1/accounts' && request.method === 'GET') {
      if (options.listStatus && options.listStatus >= 400) {
        return Promise.resolve(jsonResponse({ detail: 'boom' }, options.listStatus));
      }
      const body = url.searchParams.get('cursor')
        ? (options.page2 ?? options.page1)
        : options.page1;
      return Promise.resolve(jsonResponse(body));
    }
    if (url.pathname === '/api/v1/proxies' && request.method === 'GET') {
      return Promise.resolve(
        jsonResponse({
          proxies: [
            {
              id: 'p1',
              proxy_type: 'socks5',
              host: 'nl',
              port: 1080,
              has_password: false,
              status: 'unknown',
              used: 0,
              capacity: 3,
              free: 3,
              created_at: 'now',
              updated_at: 'now',
            },
          ],
        }),
      );
    }
    if (url.pathname === '/api/v1/accounts/check' && options.checkStatus) {
      return Promise.resolve(jsonResponse({ detail: 'boom' }, options.checkStatus));
    }
    return Promise.resolve(jsonResponse(account('acc-1')));
  });
}

test('shows the loading state first, then the table with live data', async () => {
  routeApi({ page1: { items: [account('acc-1')], next_cursor: null } });
  renderWithClient(<AccountsPage />);
  expect(screen.getByText('Загрузка…')).toBeInTheDocument();
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
});

test('shows the empty state', async () => {
  routeApi({ page1: { items: [], next_cursor: null } });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('Аккаунтов нет')).toBeInTheDocument();
  });
});

test('shows the error state', async () => {
  routeApi({ page1: {}, listStatus: 500 });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });
});

test('paginates forward with the next cursor', async () => {
  routeApi({
    page1: { items: [account('acc-1')], next_cursor: '20' },
    page2: { items: [account('acc-2')], next_cursor: null },
  });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Вперёд'));
  await waitFor(() => {
    expect(screen.getByText('acc-2')).toBeInTheDocument();
  });
});

function listGets(): number {
  return vi.mocked(fetch).mock.calls.filter(([input]) => {
    const request = input as Request;
    return new URL(request.url).pathname === '/api/v1/accounts' && request.method === 'GET';
  }).length;
}

test('typing in the search box keeps the table on screen and fires one request', async () => {
  routeApi({ page1: { items: [account('acc-1')], next_cursor: null } });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
  const before = listGets();

  await userEvent.type(screen.getByPlaceholderText('Поиск по аккаунтам…'), 'abc');

  // The generated key embeds `query`, so each keystroke was a fresh key with no
  // cached data: the table AND the pagination block were replaced by the loading
  // line on every character.
  expect(screen.queryByText('Загрузка…')).not.toBeInTheDocument();
  expect(screen.getByText('acc-1')).toBeInTheDocument();
  // ...and three keystrokes cost one request, not three.
  await waitFor(() => {
    expect(listGets()).toBe(before + 1);
  });
});

test('an emptied non-first page still offers the way back', async () => {
  routeApi({
    page1: { items: [account('acc-1')], next_cursor: '20' },
    page2: { items: [], next_cursor: null },
  });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Вперёд'));

  // Deleting the last row of page 2 lands here. Prev used to live inside the
  // non-empty branch, so it vanished with the table and the only escapes were
  // the search box and a reload.
  await waitFor(() => {
    expect(screen.getByText('Аккаунтов нет')).toBeInTheDocument();
  });
  const prev = screen.getByText('Назад');
  expect(prev).toBeEnabled();
  await userEvent.click(prev);
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
});

test('runs the check action on a row', async () => {
  routeApi({ page1: { items: [account('acc-1')], next_cursor: null } });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByTitle('Проверить'));
  await waitFor(() => {
    const checked = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/accounts/check'));
    expect(checked).toBe(true);
  });
});

// The spinner was the whole story before: a check that came back unauthorized
// looked exactly like one that came back alive, so the operator learned nothing.
test('a passing check leaves a green tick on the row button', async () => {
  routeApi({ page1: { items: [account('acc-1')], next_cursor: null } });
  const { container } = renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });

  await userEvent.click(screen.getByTitle('Проверить'));

  await waitFor(() => {
    expect(container.querySelector('button.bg-success svg')).not.toBeNull();
  });
});

test('a failed check leaves a red cross instead', async () => {
  routeApi({ page1: { items: [account('acc-1')], next_cursor: null }, checkStatus: 500 });
  const { container } = renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });

  await userEvent.click(screen.getByTitle('Проверить'));

  await waitFor(() => {
    expect(container.querySelector('button.bg-danger svg')).not.toBeNull();
  });
});

// Two rows, and every /accounts/check parked until the test releases it.
function routeTwoRowsWithParkedChecks(): ((response: Response) => void)[] {
  const releases: ((response: Response) => void)[] = [];
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/accounts/stats') {
      return Promise.resolve(
        jsonResponse({ total: 2, active: 2, idle: 0, needs_code: 0, problem: 0 }),
      );
    }
    if (url.pathname === '/api/v1/accounts' && request.method === 'GET') {
      return Promise.resolve(
        jsonResponse({ items: [account('acc-1'), account('acc-2')], next_cursor: null }),
      );
    }
    if (url.pathname === '/api/v1/accounts/check') {
      return new Promise((resolve) => {
        releases.push(resolve);
      });
    }
    return Promise.resolve(jsonResponse({}));
  });
  return releases;
}

test('checking a second row leaves the first row busy, and settling clears only its own', async () => {
  // busyId was ONE string: the second click moved it to row 2, so row 1's spinner
  // vanished and its buttons re-enabled while its check was still in flight — and
  // the first response to land then cleared row 2's spinner as well.
  const releases = routeTwoRowsWithParkedChecks();
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-2')).toBeInTheDocument();
  });
  const checks = () => screen.getAllByTitle('Проверить');

  await userEvent.click(checks()[0]!);
  await userEvent.click(checks()[1]!);
  await waitFor(() => {
    expect(releases).toHaveLength(2);
  });
  expect(checks()[0]).toBeDisabled();
  expect(checks()[1]).toBeDisabled();
  // The delete button of an in-flight row is disabled too, so the row cannot be
  // deleted from under its own check.
  expect(screen.getAllByTitle('Удалить')[0]).toBeDisabled();

  releases[0]!(jsonResponse({}));
  await waitFor(() => {
    expect(checks()[0]).toBeEnabled();
  });
  expect(checks()[1]).toBeDisabled();
});

test("a second check does not swallow the first row's list refresh", async () => {
  // check.mutate(vars, {onSettled}) put the handler in the hook's ONE observer
  // slot; the second row's click took it over, so when row 1 settled last its
  // invalidate() never ran and the table kept showing its pre-check status.
  const releases = routeTwoRowsWithParkedChecks();
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-2')).toBeInTheDocument();
  });

  await userEvent.click(screen.getAllByTitle('Проверить')[0]!);
  await userEvent.click(screen.getAllByTitle('Проверить')[1]!);
  await waitFor(() => {
    expect(releases).toHaveLength(2);
  });

  // Row 2 settles first, then row 1 — the late one must still refresh the list.
  releases[1]!(jsonResponse({}));
  await waitFor(() => {
    expect(listGets()).toBeGreaterThan(1);
  });
  const afterSecond = listGets();
  releases[0]!(jsonResponse({}));
  await waitFor(() => {
    expect(listGets()).toBeGreaterThan(afterSecond);
  });
});

test('deleting one row does not re-enable another row mid-check', async () => {
  // check and delete shared the single busyId, so confirming a delete on row 2
  // re-enabled row 1 while its check was in flight, and the delete's own settle
  // then cleared row 1's spinner instead of row 2's.
  const releases: ((response: Response) => void)[] = [];
  let deleteRelease!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/accounts/stats') {
      return Promise.resolve(
        jsonResponse({ total: 2, active: 2, idle: 0, needs_code: 0, problem: 0 }),
      );
    }
    if (url.pathname === '/api/v1/accounts' && request.method === 'GET') {
      return Promise.resolve(
        jsonResponse({ items: [account('acc-1'), account('acc-2')], next_cursor: null }),
      );
    }
    if (url.pathname === '/api/v1/accounts/check') {
      return new Promise((resolve) => {
        releases.push(resolve);
      });
    }
    if (request.method === 'DELETE') {
      return new Promise((resolve) => {
        deleteRelease = resolve;
      });
    }
    return Promise.resolve(jsonResponse({}));
  });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-2')).toBeInTheDocument();
  });

  await userEvent.click(screen.getAllByTitle('Проверить')[0]!);
  await waitFor(() => {
    expect(releases).toHaveLength(1);
  });
  // Confirm a delete on the OTHER row while row 1's check is still running.
  await userEvent.click(screen.getAllByTitle('Удалить')[1]!);
  // The only element whose TEXT is "Удалить" is the modal's confirm button (the
  // row buttons carry it as a title on an icon).
  await userEvent.click(screen.getByText('Удалить'));
  await waitFor(() => {
    expect(deleteRelease).toBeDefined();
  });

  expect(screen.getAllByTitle('Проверить')[0]).toBeDisabled();
  expect(screen.getAllByTitle('Проверить')[1]).toBeDisabled();
  // The delete finishing must not re-enable the row that is still checking.
  deleteRelease(new Response(null, { status: 204 }));
  await waitFor(() => {
    expect(screen.getAllByTitle('Проверить')[1]).toBeEnabled();
  });
  expect(screen.getAllByTitle('Проверить')[0]).toBeDisabled();
});

test("a second delete does not swallow the first row's list refresh", async () => {
  // remove.mutate(vars, {onSettled}) put the handler in the hook's ONE observer
  // slot. The delete modal closes on the same tick, so a second row's delete is
  // immediately reachable: it took the slot over and the first row's invalidate()
  // never ran, leaving the deleted account on screen.
  const releases: ((response: Response) => void)[] = [];
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/accounts/stats') {
      return Promise.resolve(
        jsonResponse({ total: 2, active: 2, idle: 0, needs_code: 0, problem: 0 }),
      );
    }
    if (url.pathname === '/api/v1/accounts' && request.method === 'GET') {
      return Promise.resolve(
        jsonResponse({ items: [account('acc-1'), account('acc-2')], next_cursor: null }),
      );
    }
    if (request.method === 'DELETE') {
      return new Promise((resolve) => {
        releases.push(resolve);
      });
    }
    return Promise.resolve(jsonResponse({}));
  });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-2')).toBeInTheDocument();
  });

  for (const index of [0, 1]) {
    await userEvent.click(screen.getAllByTitle('Удалить')[index]!);
    await userEvent.click(screen.getByText('Удалить'));
  }
  await waitFor(() => {
    expect(releases).toHaveLength(2);
  });
  // Both rows are being deleted, so neither can be acted on again.
  expect(screen.getAllByTitle('Удалить')[0]).toBeDisabled();
  expect(screen.getAllByTitle('Удалить')[1]).toBeDisabled();

  // Row 2 settles first, then row 1 — the late one must still refresh the list.
  releases[1]!(new Response(null, { status: 204 }));
  await waitFor(() => {
    expect(listGets()).toBeGreaterThan(1);
  });
  const afterSecond = listGets();
  releases[0]!(new Response(null, { status: 204 }));
  await waitFor(() => {
    expect(listGets()).toBeGreaterThan(afterSecond);
  });
});

test('the add button opens the add-account wizard', async () => {
  routeApi({ page1: { items: [account('acc-1')], next_cursor: null } });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('+ Аккаунт'));
  expect(screen.getByText('Добавить аккаунт')).toBeInTheDocument();
});

test('the profile pencil opens the profile modal for the row account', async () => {
  routeApi({ page1: { items: [account('acc-1')], next_cursor: null } });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByTitle('Редактировать профиль'));
  expect(screen.getByText('Текст')).toBeInTheDocument();
});

test('the proxy-pool add button opens the proxy-add modal', async () => {
  routeApi({ page1: { items: [account('acc-1')], next_cursor: null } });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Добавить'));
  expect(screen.getByText('Добавить прокси')).toBeInTheDocument();
});

test('the stat tiles reflect the fleet-wide stats query, not the loaded page', async () => {
  // One row on the page, but the fleet spans many accounts.
  routeApi({
    page1: { items: [account('acc-1')], next_cursor: '20' },
    stats: { total: 137, active: 90, idle: 12, needs_code: 20, problem: 15 },
  });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
  await waitFor(() => {
    // Total tile shows the fleet count (137), not items.length (1).
    expect(screen.getByText('137')).toBeInTheDocument();
  });
  expect(screen.getByText('90')).toBeInTheDocument();
  expect(screen.getByText('20')).toBeInTheDocument();
});

test('the edited account reflects the fresh row after the list refetches', async () => {
  // First list load: acc-1 is unauthorized. After opening edit and a refetch,
  // the same id comes back alive — the passed account must track the fresh row.
  const unauth: AccountRead = {
    ...account('acc-1'),
    status: 'unauthorized',
    phone: '+79990001122',
  };
  const alive: AccountRead = { ...unauth, status: 'alive' };
  let call = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/accounts/stats') {
      return Promise.resolve(
        jsonResponse({ total: 1, active: 1, idle: 0, needs_code: 0, problem: 0 }),
      );
    }
    if (url.pathname === '/api/v1/accounts' && request.method === 'GET') {
      call += 1;
      return Promise.resolve(
        jsonResponse({ items: [call === 1 ? unauth : alive], next_cursor: null }),
      );
    }
    return Promise.resolve(jsonResponse(account('acc-1')));
  });

  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AccountsPage />
    </QueryClientProvider>,
  );
  await waitFor(() => {
    expect(screen.getByText('+79990001122')).toBeInTheDocument();
  });
  // Open the edit view for the (stale) unauthorized row.
  await userEvent.click(screen.getByText('+79990001122'));
  await waitFor(() => {
    expect(screen.getByText('Не авторизован')).toBeInTheDocument();
  });
  // A refetch flips the row to alive; the derived account passed to edit updates.
  await client.invalidateQueries();
  await waitFor(() => {
    expect(screen.getByText('Активен')).toBeInTheDocument();
  });
  expect(screen.queryByText('Не авторизован')).not.toBeInTheDocument();
});

test('the profile modal tracks the fresh row after the list refetches', async () => {
  // Like the edit view, the profile modal derives its account from the live
  // list — a refetched phone must show up in the open modal, not the snapshot
  // captured at click time.
  const before: AccountRead = { ...account('acc-1'), phone: '+70000000001' };
  const after: AccountRead = { ...before, phone: '+70000000002' };
  let call = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/accounts/stats') {
      return Promise.resolve(
        jsonResponse({ total: 1, active: 1, idle: 0, needs_code: 0, problem: 0 }),
      );
    }
    if (url.pathname === '/api/v1/accounts' && request.method === 'GET') {
      call += 1;
      return Promise.resolve(
        jsonResponse({ items: [call === 1 ? before : after], next_cursor: null }),
      );
    }
    return Promise.resolve(jsonResponse(account('acc-1')));
  });

  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AccountsPage />
    </QueryClientProvider>,
  );
  await waitFor(() => {
    expect(screen.getByText('+70000000001')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByTitle('Редактировать профиль'));
  expect(screen.getByText('Текст')).toBeInTheDocument();

  await client.invalidateQueries();
  // Both the row and the open modal now carry the fresh phone.
  await waitFor(() => {
    expect(screen.getAllByText(/\+70000000002/).length).toBeGreaterThanOrEqual(2);
  });
});

test('the open profile modal survives the account dropping out of the filtered list', async () => {
  // A refetch (e.g. after a rename) can drop the row from the current page;
  // the open modal must keep the click-time row instead of vanishing.
  const row: AccountRead = { ...account('acc-1'), phone: '+70000000003' };
  let call = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/accounts/stats') {
      return Promise.resolve(
        jsonResponse({ total: 1, active: 1, idle: 0, needs_code: 0, problem: 0 }),
      );
    }
    if (url.pathname === '/api/v1/accounts' && request.method === 'GET') {
      call += 1;
      return Promise.resolve(jsonResponse({ items: call === 1 ? [row] : [], next_cursor: null }));
    }
    return Promise.resolve(jsonResponse(account('acc-1')));
  });

  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AccountsPage />
    </QueryClientProvider>,
  );
  await waitFor(() => {
    expect(screen.getByText('+70000000003')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByTitle('Редактировать профиль'));
  expect(screen.getByText('Текст')).toBeInTheDocument();

  await client.invalidateQueries();
  // The page shows the empty state, but the modal stays open on the old row.
  await waitFor(() => {
    expect(screen.getByText('Аккаунтов нет')).toBeInTheDocument();
  });
  expect(screen.getByText('Текст')).toBeInTheDocument();
  // The modal header still carries the click-time row's phone.
  expect(screen.getAllByText(/\+70000000003/).length).toBeGreaterThanOrEqual(1);
});
