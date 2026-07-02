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
    return Promise.resolve(jsonResponse({}));
  });
}

function fileInput(): HTMLInputElement {
  return document.body.querySelector('input[type="file"]') as HTMLInputElement;
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
