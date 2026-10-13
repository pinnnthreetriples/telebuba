import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { AddAccountModal } from './AddAccountModal';

// The wizard's device pick: desktop by default, mobile only when chosen, and "both"
// adds the other device as its own sign-in in a step AFTER the proxy step.

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

function account(id: string, extra: Record<string, unknown> = {}) {
  return { account_id: id, status: 'new', created_at: 'n', updated_at: 'n', ...extra };
}

// Every request the wizard sends, with the session device each import carried.
function routeApi(deviceAnswer: () => Response = () => jsonResponse(account('a'))) {
  const sessionDevices: (string | null)[] = [];
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/import-session') {
      const form = await request.formData();
      sessionDevices.push(form.get('session_device') as string | null);
      return jsonResponse(account((form.get('file') as File).name.replace('.session', '')));
    }
    if (pathname === '/api/v1/accounts/start-login') {
      return jsonResponse(account('79990001122', { phone: '+79990001122' }));
    }
    if (pathname.endsWith('/request-code')) {
      return jsonResponse({ account_id: '79990001122', phone: '+79990001122' });
    }
    if (pathname.endsWith('/submit-code')) return jsonResponse(account('79990001122'));
    if (pathname.endsWith('/devices')) return deviceAnswer();
    return jsonResponse({});
  });
  return sessionDevices;
}

function calls(fragment: string): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => request.url.includes(fragment));
}

// The one request a step sent; fails loudly when there is none.
function only(requests: Request[]): Request {
  const [request] = requests;
  if (requests.length !== 1 || !request)
    throw new Error(`expected one request, got ${String(requests.length)}`);
  return request;
}

function pickSession(name: string): void {
  fireEvent.change(document.body.querySelector('input[type="file"]') as HTMLInputElement, {
    target: { files: [new File(['x'], name, { type: 'application/octet-stream' })] },
  });
}

function deviceGroup(name = 'Устройство') {
  return screen.getByRole('radiogroup', { name });
}

async function importAndSkipProxy(name: string): Promise<void> {
  pickSession(name);
  const next = screen.getByText('Далее');
  await waitFor(() => {
    expect(next).toBeEnabled();
  });
  await userEvent.click(next);
  await userEvent.click(screen.getByText('Пропустить'));
}

test('a session import is desktop by default and has no second-device step', async () => {
  const sessionDevices = routeApi();
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);
  await userEvent.click(screen.getByText('Файл .session'));
  expect(within(deviceGroup()).getByRole('radio', { name: 'ПК' })).toBeChecked();

  await importAndSkipProxy('a.session');

  expect(sessionDevices).toEqual(['desktop']);
  expect(await screen.findByText('Шаг 3 · облачный пароль')).toBeInTheDocument();
});

test('a mobile pick imports the file as a mobile session', async () => {
  const sessionDevices = routeApi();
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);
  await userEvent.click(screen.getByText('Файл .session'));
  await userEvent.click(within(deviceGroup()).getByRole('radio', { name: 'Телефон' }));

  pickSession('a.session');
  await waitFor(() => {
    expect(screen.getByText('Далее')).toBeEnabled();
  });

  expect(sessionDevices).toEqual(['mobile']);
  // The pick is the account's device now: changing it would change nothing.
  expect(within(deviceGroup()).getByRole('radio', { name: 'ПК' })).toBeDisabled();
});

test('tdata offers desktop or both, never mobile only', async () => {
  routeApi();
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);
  await userEvent.click(screen.getByText('Номер телефона'));
  await userEvent.click(within(deviceGroup()).getByRole('radio', { name: 'Телефон' }));

  await userEvent.click(screen.getByText('Архив tdata.zip'));

  const group = deviceGroup();
  expect(within(group).getByRole('radio', { name: 'Телефон' })).toBeDisabled();
  expect(within(group).getByRole('radio', { name: 'ПК' })).toBeChecked();
});

test('both with a mobile .session file adds a desktop sign-in after the proxy step', async () => {
  const sessionDevices = routeApi();
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);
  await userEvent.click(screen.getByText('Файл .session'));
  await userEvent.click(within(deviceGroup()).getByRole('radio', { name: 'Оба' }));
  await userEvent.click(
    within(deviceGroup('Файл .session — с устройства')).getByRole('radio', { name: 'Телефон' }),
  );

  await importAndSkipProxy('a.session');

  expect(sessionDevices).toEqual(['mobile']);
  expect(screen.getByText('Шаг 3 · второе устройство')).toBeInTheDocument();
  expect(calls('/devices')).toHaveLength(0);

  await userEvent.click(screen.getByText('Войти как «ПК»'));

  expect(await screen.findByText('устройство добавлено')).toBeInTheDocument();
  const request = only(calls('/devices'));
  expect(new URL(request.url).pathname).toBe('/api/v1/accounts/a/devices');
  expect(await request.clone().json()).toEqual({ device_type: 'desktop' });
  await userEvent.click(screen.getByText('Далее'));
  expect(await screen.findByText('Шаг 4 · облачный пароль')).toBeInTheDocument();
});

test('a refused second sign-in says why and can be retried', async () => {
  let refuse = true;
  routeApi(() =>
    refuse
      ? jsonResponse({ error: { code: 'bad_request', message: 'device_code_timeout' } }, 400)
      : jsonResponse(account('a')),
  );
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);
  await userEvent.click(screen.getByText('Файл .session'));
  await userEvent.click(within(deviceGroup()).getByRole('radio', { name: 'Оба' }));
  await importAndSkipProxy('a.session');

  await userEvent.click(screen.getByText('Войти как «Телефон»'));

  expect(
    await screen.findByText('Код входа не пришёл в основную сессию вовремя.'),
  ).toBeInTheDocument();
  refuse = false;
  await userEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  expect(await screen.findByText('устройство добавлено')).toBeInTheDocument();
  expect(calls('/devices')).toHaveLength(2);
});

test('a phone login with both signs in as desktop, then adds mobile after the code', async () => {
  routeApi();
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);
  await userEvent.click(screen.getByText('Номер телефона'));
  await userEvent.click(within(deviceGroup()).getByRole('radio', { name: 'Оба' }));
  await userEvent.type(screen.getByPlaceholderText('+7 999 000-11-22'), '+79990001122');
  await userEvent.click(screen.getByText('Продолжить'));
  const next = screen.getByText('Далее');
  await waitFor(() => {
    expect(next).toBeEnabled();
  });
  const login = only(calls('/accounts/start-login'));
  expect(await login.clone().json()).toEqual({ phone: '+79990001122', session_device: 'desktop' });

  await userEvent.click(next);
  await userEvent.click(screen.getByText('Пропустить'));
  await userEvent.click(screen.getByText('Отправить код'));
  await userEvent.type(await screen.findByLabelText('Код из SMS'), '11111');
  await userEvent.click(screen.getByText('Подтвердить вход'));

  expect(await screen.findByText('Шаг 4 · второе устройство')).toBeInTheDocument();
  await userEvent.click(screen.getByText('Войти как «Телефон»'));
  await screen.findByText('устройство добавлено');
  const device = only(calls('/devices'));
  expect(await device.clone().json()).toEqual({ device_type: 'mobile' });
});
