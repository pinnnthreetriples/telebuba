import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { AddAccountModal } from './AddAccountModal';

// The wizard with MANY files picked at once: one request per file, "Next" waits
// for the whole batch, step 2 speaks in counts, and a manual proxy is assigned
// to every created account.

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const POOL_PROXY = {
  id: 'pool-1',
  proxy_type: 'socks5',
  host: 'nl-1.proxyhub.net',
  port: 1080,
  has_password: false,
  status: 'tcp_working',
  country_code: 'nl',
  created_at: 'now',
  updated_at: 'now',
  used: 0,
  capacity: 3,
  free: 3,
};

function account(id: string) {
  return { account_id: id, status: 'new', created_at: 'n', updated_at: 'n' };
}

// Each import-session answers with the account named after its file.
function routeApi() {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/proxies' && request.method === 'GET') {
      return jsonResponse({ proxies: [POOL_PROXY] });
    }
    if (pathname === '/api/v1/proxies') return jsonResponse(POOL_PROXY);
    if (pathname.endsWith('/assign')) return jsonResponse(POOL_PROXY);
    if (pathname === '/api/v1/accounts/import-session') {
      const file = (await request.formData()).get('file') as File;
      return jsonResponse(account(file.name.replace('.session', '')));
    }
    return jsonResponse({});
  });
}

function pickSessions(...names: string[]): void {
  fireEvent.change(document.body.querySelector('input[type="file"]') as HTMLInputElement, {
    target: {
      files: names.map((name) => new File(['x'], name, { type: 'application/octet-stream' })),
    },
  });
}

async function requests(fragment: string): Promise<Request[]> {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => request.url.includes(fragment));
}

test('three session files → three imports, Next waits for the batch, step 2 counts them', async () => {
  routeApi();
  const onImported = vi.fn();
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={onImported} />);
  await userEvent.click(screen.getByText('Файл .session'));
  expect(document.body.querySelector('input[type="file"]')).toHaveAttribute('multiple');
  pickSessions('a.session', 'b.session', 'c.session');

  expect(screen.getByText('a.session')).toBeInTheDocument();
  expect(screen.getByText('c.session')).toBeInTheDocument();
  await waitFor(() => {
    expect(screen.getByText('Добавлено 3 из 3')).toBeInTheDocument();
  });
  expect(await requests('/accounts/import-session')).toHaveLength(3);
  expect(onImported).toHaveBeenCalledTimes(3);

  await userEvent.click(screen.getByText('Далее'));
  expect(
    screen.getByText('Добавлено аккаунтов: 3. Назначьте прокси для работы.'),
  ).toBeInTheDocument();
});

test('one failed file keeps the others; Next unlocks on the survivors and retry recovers it', async () => {
  let failB = true;
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    if (new URL(request.url).pathname !== '/api/v1/accounts/import-session') {
      return jsonResponse({});
    }
    const file = (await request.formData()).get('file') as File;
    if (file.name === 'b.session' && failB) {
      return jsonResponse({ error: { code: 'conflict', message: 'exists' } }, 409);
    }
    return jsonResponse(account(file.name.replace('.session', '')));
  });
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);
  await userEvent.click(screen.getByText('Файл .session'));
  pickSessions('a.session', 'b.session');

  await waitFor(() => {
    expect(screen.getByText('Добавлено 1 из 2')).toBeInTheDocument();
  });
  expect(screen.getByText('Не удалось импортировать')).toBeInTheDocument();
  // Nothing in flight and one account exists — the operator may go on without b.
  expect(screen.getByText('Далее')).toBeEnabled();

  failB = false;
  await userEvent.click(screen.getByText('Повторить'));
  await waitFor(() => {
    expect(screen.getByText('Добавлено 2 из 2')).toBeInTheDocument();
  });
  expect(await requests('/accounts/import-session')).toHaveLength(3);
});

test('Next stays locked while any file of the batch is still importing', async () => {
  let resolveB!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    if (new URL(request.url).pathname !== '/api/v1/accounts/import-session') {
      return jsonResponse({});
    }
    const file = (await request.formData()).get('file') as File;
    if (file.name === 'b.session') {
      return new Promise((resolve) => {
        resolveB = resolve;
      });
    }
    return jsonResponse(account('a'));
  });
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);
  await userEvent.click(screen.getByText('Файл .session'));
  pickSessions('a.session', 'b.session');

  await waitFor(() => {
    expect(screen.getByText('Добавлено 1 из 2')).toBeInTheDocument();
  });
  // Half a batch on step 2 would assign proxies to half the accounts.
  expect(screen.getByText('Далее')).toBeDisabled();
  resolveB(jsonResponse(account('b')));
  await waitFor(() => {
    expect(screen.getByText('Далее')).toBeEnabled();
  });
});

test('manual proxy is created once and assigned to EVERY imported account before closing', async () => {
  routeApi();
  const onClose = vi.fn();
  renderWithClient(<AddAccountModal onClose={onClose} onImported={vi.fn()} />);
  await userEvent.click(screen.getByText('Файл .session'));
  pickSessions('a.session', 'b.session');
  await waitFor(() => {
    expect(screen.getByText('Далее')).toBeEnabled();
  });
  await userEvent.click(screen.getByText('Далее'));
  await userEvent.click(screen.getByText('Добавить прокси'));
  await userEvent.type(screen.getByLabelText('Хост'), '1.2.3.4');
  await userEvent.type(screen.getByLabelText('Порт'), '1080');
  await waitFor(() => {
    expect(screen.getByText('Готово')).toBeEnabled();
  });
  await userEvent.click(screen.getByText('Готово'));

  await waitFor(() => {
    expect(onClose).toHaveBeenCalled();
  });
  const assigns = await requests('/proxies/pool-1/assign');
  const bodies = await Promise.all(assigns.map(async (request) => request.json()));
  expect(bodies.map((body: { account_id: string }) => body.account_id).sort()).toEqual(['a', 'b']);
  const created = (await requests('/api/v1/proxies')).filter(
    (request) => request.method === 'POST' && new URL(request.url).pathname === '/api/v1/proxies',
  );
  expect(created).toHaveLength(1);
});

test('pool step distributes the batch and closes on Done', async () => {
  routeApi();
  const onClose = vi.fn();
  renderWithClient(<AddAccountModal onClose={onClose} onImported={vi.fn()} />);
  await userEvent.click(screen.getByText('Файл .session'));
  pickSessions('a.session', 'b.session');
  await waitFor(() => {
    expect(screen.getByText('Далее')).toBeEnabled();
  });
  await userEvent.click(screen.getByText('Далее'));
  await userEvent.click(screen.getByText('Выбрать из пула'));
  await userEvent.click(await screen.findByText('Распределить автоматически'));
  await waitFor(() => {
    expect(screen.getByText('Назначено: 2 из 2')).toBeInTheDocument();
  });
  // More than one account never auto-closes: the operator reads the tally first.
  expect(onClose).not.toHaveBeenCalled();
  await userEvent.click(screen.getByText('Готово'));
  expect(onClose).toHaveBeenCalled();
});
