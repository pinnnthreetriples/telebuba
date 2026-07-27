import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { AddAccountModal } from './AddAccountModal';

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

function routeApi() {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/proxies' && request.method === 'GET') {
      return Promise.resolve(jsonResponse({ proxies: [POOL_PROXY] }));
    }
    if (pathname === '/api/v1/proxies') return Promise.resolve(jsonResponse(POOL_PROXY));
    if (pathname.endsWith('/assign')) return Promise.resolve(jsonResponse(POOL_PROXY));
    if (pathname === '/api/v1/accounts/import-tdata') {
      return Promise.resolve(
        jsonResponse({
          accounts: [{ account_id: 'imp', status: 'new', created_at: 'n', updated_at: 'n' }],
        }),
      );
    }
    if (pathname === '/api/v1/accounts/import-session') {
      return Promise.resolve(
        jsonResponse({ account_id: 'imp', status: 'new', created_at: 'n', updated_at: 'n' }),
      );
    }
    if (pathname === '/api/v1/accounts/start-login') {
      return Promise.resolve(
        jsonResponse({
          account_id: '79990001122',
          status: 'new',
          phone: '+79990001122',
          created_at: 'n',
          updated_at: 'n',
        }),
      );
    }
    if (pathname.endsWith('/request-code')) {
      return Promise.resolve(jsonResponse({ account_id: '79990001122', phone: '+79990001122' }));
    }
    if (pathname.endsWith('/submit-code')) {
      return Promise.resolve(
        jsonResponse({
          account_id: '79990001122',
          status: 'alive',
          created_at: 'n',
          updated_at: 'n',
        }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });
}

function fileInput(): HTMLInputElement {
  return document.body.querySelector('input[type="file"]') as HTMLInputElement;
}

function calls(fragment: string): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => request.url.includes(fragment));
}

function pickSession(): void {
  fireEvent.change(fileInput(), {
    target: { files: [new File(['x'], 'acc.session', { type: 'application/octet-stream' })] },
  });
}

test('stepper navigates method → choice → manual/pool → back to step 1', async () => {
  routeApi();
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);
  expect(screen.getByText('Добавить аккаунт')).toBeInTheDocument();

  const next = screen.getByText('Далее');
  expect(next).toBeDisabled();
  await userEvent.click(screen.getByText('Файл .session'));
  // Next stays disabled until an import actually succeeds.
  expect(next).toBeDisabled();
  fireEvent.change(fileInput(), {
    target: { files: [new File(['x'], 'acc.session', { type: 'application/octet-stream' })] },
  });
  await waitFor(() => {
    expect(next).toBeEnabled();
  });
  await userEvent.click(next);
  expect(screen.getByText('Аккаунт добавлен. Назначьте прокси для работы.')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Добавить прокси'));
  expect(screen.getByText('Хост')).toBeInTheDocument();
  await userEvent.click(screen.getByText('Назад'));

  await userEvent.click(screen.getByText('Выбрать из пула'));
  await waitFor(() => {
    expect(screen.getByText('nl-1.proxyhub.net:1080')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('Назад'));

  await userEvent.click(screen.getByText('Назад'));
  expect(screen.getByText('Шаг 1 · способ добавления')).toBeInTheDocument();
});

test('tdata upload imports the account', async () => {
  routeApi();
  const onImported = vi.fn();
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={onImported} />);
  await userEvent.click(screen.getByText('Архив tdata.zip'));
  fireEvent.change(fileInput(), {
    target: { files: [new File(['x'], 'acc.zip', { type: 'application/zip' })] },
  });
  expect(screen.getByText('acc.zip')).toBeInTheDocument();
  await waitFor(() => {
    const imported = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/accounts/import-tdata'));
    expect(imported).toBe(true);
  });
  await waitFor(() => {
    expect(onImported).toHaveBeenCalled();
  });
});

test('session upload imports then a pool proxy is assigned', async () => {
  routeApi();
  const onClose = vi.fn();
  const onImported = vi.fn();
  renderWithClient(<AddAccountModal onClose={onClose} onImported={onImported} />);
  await userEvent.click(screen.getByText('Файл .session'));
  fireEvent.change(fileInput(), {
    target: { files: [new File(['x'], 'acc.session', { type: 'application/octet-stream' })] },
  });
  await waitFor(() => {
    const imported = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/accounts/import-session'));
    expect(imported).toBe(true);
  });

  await userEvent.click(screen.getByText('Далее'));
  await userEvent.click(screen.getByText('Выбрать из пула'));
  await waitFor(() => {
    expect(screen.getByText('nl-1.proxyhub.net:1080')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByText('nl-1.proxyhub.net:1080'));
  await waitFor(() => {
    const assigned = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/proxies/pool-1/assign'));
    expect(assigned).toBe(true);
  });
  expect(onClose).toHaveBeenCalled();
});

test('manual proxy form creates and assigns on done', async () => {
  routeApi();
  const onClose = vi.fn();
  renderWithClient(<AddAccountModal onClose={onClose} onImported={vi.fn()} />);
  await userEvent.click(screen.getByText('Файл .session'));
  fireEvent.change(fileInput(), {
    target: { files: [new File(['x'], 'acc.session', { type: 'application/octet-stream' })] },
  });
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
    const created = vi.mocked(fetch).mock.calls.some(([input]) => {
      const request = input as Request;
      return new URL(request.url).pathname === '/api/v1/proxies' && request.method === 'POST';
    });
    expect(created).toBe(true);
  });
});

test('a failed import shows the error state and keeps Next disabled', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/accounts/import-session') {
      return Promise.reject(new Error('boom'));
    }
    return Promise.resolve(jsonResponse({}));
  });
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);
  await userEvent.click(screen.getByText('Файл .session'));
  fireEvent.change(fileInput(), {
    target: { files: [new File(['x'], 'acc.session', { type: 'application/octet-stream' })] },
  });
  // The file card reports the failure instead of a premature "File ready".
  expect(await screen.findByText('Не удалось импортировать')).toBeInTheDocument();
  expect(screen.getByText('Далее')).toBeDisabled();
});

test('phone method: create account → skip proxy → request + confirm code', async () => {
  routeApi();
  const onClose = vi.fn();
  const onImported = vi.fn();
  renderWithClient(<AddAccountModal onClose={onClose} onImported={onImported} />);

  await userEvent.click(screen.getByText('Номер телефона'));
  await userEvent.type(screen.getByPlaceholderText('+7 999 000-11-22'), '+79990001122');
  await userEvent.click(screen.getByText('Продолжить'));

  // start-login provisions the account and unlocks Next.
  await waitFor(() => {
    const started = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/accounts/start-login'));
    expect(started).toBe(true);
  });
  const next = screen.getByText('Далее');
  await waitFor(() => {
    expect(next).toBeEnabled();
  });
  await userEvent.click(next);

  // Proxy step (step 2) — skip straight to the code step.
  await userEvent.click(screen.getByText('Пропустить'));
  expect(screen.getByText('Шаг 3 · вход по коду')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Отправить код'));
  await waitFor(() => {
    const requested = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/request-code'));
    expect(requested).toBe(true);
  });

  await userEvent.type(await screen.findByLabelText('Код из SMS'), '11111');
  await userEvent.click(screen.getByText('Подтвердить вход'));
  await waitFor(() => {
    const submitted = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/submit-code'));
    expect(submitted).toBe(true);
  });
  expect(onClose).toHaveBeenCalled();
});

test('phone method: a failed start-login shows the error and keeps Next disabled', async () => {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/accounts/start-login') {
      return Promise.reject(new Error('boom'));
    }
    return Promise.resolve(jsonResponse({}));
  });
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);
  await userEvent.click(screen.getByText('Номер телефона'));
  await userEvent.type(screen.getByPlaceholderText('+7 999 000-11-22'), '+79990001122');
  await userEvent.click(screen.getByText('Продолжить'));
  expect(await screen.findByText('Не удалось создать аккаунт')).toBeInTheDocument();
  expect(screen.getByText('Далее')).toBeDisabled();
});

test('switching method after an account was created re-locks Next', async () => {
  routeApi();
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);

  // Phone provisions the account; the sign-in is step 3, still ahead.
  await userEvent.click(screen.getByText('Номер телефона'));
  await userEvent.type(screen.getByPlaceholderText('+7 999 000-11-22'), '+79990001122');
  await userEvent.click(screen.getByText('Продолжить'));
  await waitFor(() => {
    expect(screen.getByText('Далее')).toBeEnabled();
  });

  // Step 2, back to step 1, then pick a file method without importing anything.
  await userEvent.click(screen.getByText('Далее'));
  await userEvent.click(screen.getByText('Назад'));
  await userEvent.click(screen.getByText('Файл .session'));

  // Pre-fix the phone account still unlocked Next with no file card to show for
  // it; step 2 announced "account added" and Skip closed the wizard from there,
  // because afterProxy branches on the NEW method — step 3 never rendered and
  // the phone account was left permanently signed out.
  expect(screen.getByText('Далее')).toBeDisabled();
  expect(
    screen.queryByText('Аккаунт добавлен. Назначьте прокси для работы.'),
  ).not.toBeInTheDocument();
});

test('re-clicking the ALREADY selected method keeps the imported account', async () => {
  routeApi();
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);

  await userEvent.click(screen.getByText('Файл .session'));
  pickSession();
  await waitFor(() => {
    expect(screen.getByText('Далее')).toBeEnabled();
  });

  // The account really exists now, and the only in-wizard recovery would be
  // re-importing the same file — which the backend refuses ("already exists.
  // Delete it before importing."). So an identity re-click that un-provisioned
  // the wizard left step 2 unreachable with nothing to do but × and delete the
  // account by hand.
  await userEvent.click(screen.getByText('Файл .session'));
  expect(screen.getByText('Далее')).toBeEnabled();
  expect(screen.getByText('acc.session')).toBeInTheDocument();
  expect(screen.getByText('Аккаунт импортирован')).toBeInTheDocument();
  expect(calls('/accounts/import-session')).toHaveLength(1);
});

test('an import landing after a method switch does not provision the wizard', async () => {
  let resolveImport!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === '/api/v1/accounts/import-session') {
      return new Promise((resolve) => {
        resolveImport = resolve;
      });
    }
    return Promise.resolve(jsonResponse({}));
  });
  const onImported = vi.fn();
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={onImported} />);

  await userEvent.click(screen.getByText('Файл .session'));
  pickSession();
  // Switch method while the import is still in flight.
  await userEvent.click(screen.getByText('Номер телефона'));
  resolveImport(
    jsonResponse({ account_id: 'imp', status: 'new', created_at: 'n', updated_at: 'n' }),
  );
  // onSettled runs after onSuccess, so this proves the callbacks have run.
  await waitFor(() => {
    expect(onImported).toHaveBeenCalled();
  });

  // The mutate-level onSuccess is never cancelled. Adopting its id here would
  // unlock "Next" for the PHONE method, and afterProxy would then fire
  // request-code at the already-authorised .session account.
  expect(screen.getByText('Далее')).toBeDisabled();
  expect(screen.getByText('Продолжить')).toBeInTheDocument();
  expect(screen.queryByText('Аккаунт создан')).not.toBeInTheDocument();
});

test('cancel on step 1 closes', async () => {
  routeApi();
  const onClose = vi.fn();
  renderWithClient(<AddAccountModal onClose={onClose} onImported={vi.fn()} />);
  await userEvent.click(screen.getByText('Отмена'));
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('skip on the proxy choice closes', async () => {
  routeApi();
  const onClose = vi.fn();
  renderWithClient(<AddAccountModal onClose={onClose} onImported={vi.fn()} />);
  await userEvent.click(screen.getByText('Файл .session'));
  fireEvent.change(fileInput(), {
    target: { files: [new File(['x'], 'acc.session', { type: 'application/octet-stream' })] },
  });
  await waitFor(() => {
    expect(screen.getByText('Далее')).toBeEnabled();
  });
  await userEvent.click(screen.getByText('Далее'));
  await userEvent.click(screen.getByText('Пропустить'));
  expect(onClose).toHaveBeenCalledTimes(1);
});
