import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { AddAccountModal } from './AddAccountModal';

// The QR method: step 1 creates a placeholder account, the proxy step comes before the
// first Telegram connection, and the QR step shows the code. Closing over an open QR
// sign-in cancels it.

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

const PLACEHOLDER = { account_id: 'qr-abc', status: 'new', created_at: 'n', updated_at: 'n' };

function routeApi() {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/start-qr-login') {
      return Promise.resolve(jsonResponse(PLACEHOLDER));
    }
    if (pathname === '/api/v1/accounts/qr-abc/qr-login' && request.method === 'DELETE') {
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    if (pathname === '/api/v1/accounts/qr-abc/qr-login') {
      return Promise.resolve(
        jsonResponse({
          account_id: 'qr-abc',
          status: 'waiting',
          qr_image: 'data:image/svg+xml;base64,AAAA',
        }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });
}

function calls(fragment: string, method?: string): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter(
      (request) =>
        request.url.includes(fragment) && (method === undefined || request.method === method),
    );
}

async function createPlaceholder(): Promise<void> {
  await userEvent.click(screen.getByText('QR-код'));
  await userEvent.click(screen.getByText('Продолжить'));
  await waitFor(() => {
    expect(screen.getByText('Далее')).toBeEnabled();
  });
}

test('Continue creates the placeholder account for the desktop device', async () => {
  routeApi();
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);

  await createPlaceholder();

  const [started] = calls('/accounts/start-qr-login');
  expect(calls('/accounts/start-qr-login')).toHaveLength(1);
  expect(await started?.clone().json()).toEqual({ session_device: 'desktop' });
  expect(screen.getByText('Аккаунт создан')).toBeInTheDocument();
});

test('a mobile pick starts the QR login as a mobile device', async () => {
  routeApi();
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);
  await userEvent.click(screen.getByText('QR-код'));
  const group = screen.getByRole('radiogroup', { name: 'Устройство' });
  await userEvent.click(within(group).getByRole('radio', { name: 'Телефон' }));
  await userEvent.click(screen.getByText('Продолжить'));

  await waitFor(() => {
    expect(calls('/accounts/start-qr-login')).toHaveLength(1);
  });
  const [started] = calls('/accounts/start-qr-login');
  expect(await started?.clone().json()).toEqual({ session_device: 'mobile' });
});

test('Next stays locked until the placeholder account exists', async () => {
  routeApi();
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);

  await userEvent.click(screen.getByText('QR-код'));

  expect(screen.getByText('Далее')).toBeDisabled();
});

test('a refused start shows the error and keeps Next locked', async () => {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(jsonResponse({ error: { code: 'bad_request', message: 'x' } }, 400)),
  );
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);

  await userEvent.click(screen.getByText('QR-код'));
  await userEvent.click(screen.getByText('Продолжить'));

  expect(await screen.findByText('Не удалось создать аккаунт')).toBeInTheDocument();
  expect(screen.getByText('Далее')).toBeDisabled();
});

test('the QR step comes after the proxy step and shows the code on request', async () => {
  routeApi();
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);
  await createPlaceholder();
  await userEvent.click(screen.getByText('Далее'));

  // The proxy step is first, and nothing has connected to Telegram yet.
  expect(screen.getByText('Шаг 2 · прокси')).toBeInTheDocument();
  expect(calls('/qr-login')).toHaveLength(0);
  await userEvent.click(screen.getByText('Пропустить'));

  expect(await screen.findByText('Шаг 3 · вход по QR-коду')).toBeInTheDocument();
  await userEvent.click(screen.getByText('Показать QR-код'));
  expect(await screen.findByAltText('QR-код для входа в Telegram')).toBeInTheDocument();
  expect(calls('/accounts/qr-abc/qr-login', 'POST')).toHaveLength(1);
});

test('closing over the open QR step cancels the sign-in', async () => {
  routeApi();
  const onClose = vi.fn();
  renderWithClient(<AddAccountModal onClose={onClose} onImported={vi.fn()} />);
  await createPlaceholder();
  await userEvent.click(screen.getByText('Далее'));
  await userEvent.click(screen.getByText('Пропустить'));
  await screen.findByText('Шаг 3 · вход по QR-коду');
  await userEvent.click(screen.getByText('Показать QR-код'));
  await screen.findByAltText('QR-код для входа в Telegram');

  await userEvent.click(screen.getByLabelText('Закрыть'));

  expect(onClose).toHaveBeenCalledTimes(1);
  await waitFor(() => {
    expect(calls('/accounts/qr-abc/qr-login', 'DELETE')).toHaveLength(1);
  });
});

test('closing before the QR step has nothing to cancel', async () => {
  routeApi();
  const onClose = vi.fn();
  renderWithClient(<AddAccountModal onClose={onClose} onImported={vi.fn()} />);
  await createPlaceholder();

  await userEvent.click(screen.getByLabelText('Закрыть'));

  expect(onClose).toHaveBeenCalledTimes(1);
  expect(calls('/qr-login', 'DELETE')).toHaveLength(0);
});
