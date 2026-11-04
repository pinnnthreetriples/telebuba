import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { QrLoginStep } from './QrLoginStep';

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

const QR = 'data:image/svg+xml;base64,AAAA';
const URL_BASE = '/api/v1/accounts/qr-1/qr-login';
const SAVED = { account_id: 'qr-1', status: 'alive', created_at: 'n', updated_at: 'n' };

function view(status: string, extra: Record<string, unknown> = {}) {
  return { account_id: 'qr-1', status, ...extra };
}

type Answer = () => Response;

// One scripted answer per endpoint; a function is read at request time so a test can
// change what the NEXT poll or restart sees.
function route(answers: { begin?: Answer; poll?: Answer; password?: Answer }) {
  vi.mocked(fetch).mockImplementation((input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === `${URL_BASE}/password` && answers.password) {
      return Promise.resolve(answers.password());
    }
    if (pathname === URL_BASE && request.method === 'POST' && answers.begin) {
      return Promise.resolve(answers.begin());
    }
    if (pathname === URL_BASE && request.method === 'GET' && answers.poll) {
      return Promise.resolve(answers.poll());
    }
    return Promise.resolve(jsonResponse({}));
  });
}

function calls(method: string, suffix = ''): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => request.method === method && request.url.endsWith(URL_BASE + suffix));
}

test('nothing is requested until the operator asks for the QR code', () => {
  route({});
  renderWithClient(<QrLoginStep accountId="qr-1" onDone={vi.fn()} />);

  expect(screen.getByText('Показать QR-код')).toBeInTheDocument();
  expect(vi.mocked(fetch)).not.toHaveBeenCalled();
});

test('the click starts the sign-in and the QR image appears', async () => {
  route({
    begin: () => jsonResponse(view('waiting', { qr_image: QR })),
    poll: () => jsonResponse(view('waiting', { qr_image: QR })),
  });
  renderWithClient(<QrLoginStep accountId="qr-1" onDone={vi.fn()} />);

  await userEvent.click(screen.getByText('Показать QR-код'));

  const image = await screen.findByAltText('QR-код для входа в Telegram');
  expect(image).toHaveAttribute('src', QR);
  expect(calls('POST')).toHaveLength(1);
  expect(screen.getByText('Код обновляется автоматически')).toBeInTheDocument();
});

test('the poll switches to the password form, and a wrong password says so', async () => {
  route({
    begin: () => jsonResponse(view('waiting', { qr_image: QR })),
    poll: () => jsonResponse(view('password_needed')),
    password: () => jsonResponse(view('password_needed', { error_code: 'password_invalid' })),
  });
  renderWithClient(<QrLoginStep accountId="qr-1" onDone={vi.fn()} />);
  await userEvent.click(screen.getByText('Показать QR-код'));

  const field = await screen.findByLabelText('Облачный пароль (2FA)');
  expect(screen.getByText('На аккаунте включён облачный пароль')).toBeInTheDocument();
  await userEvent.type(field, 'wrong');
  await userEvent.click(screen.getByText('Войти'));

  expect(await screen.findByText('Неверный пароль')).toBeInTheDocument();
  const [sent] = calls('POST', '/password');
  expect(sent).toBeDefined();
  expect(await sent?.clone().json()).toEqual({ password: 'wrong' });
});

test('a right password saves the account and reports done once', async () => {
  const onDone = vi.fn();
  route({
    begin: () => jsonResponse(view('waiting', { qr_image: QR })),
    poll: () => jsonResponse(view('password_needed')),
    password: () => jsonResponse(view('alive', { account: SAVED })),
  });
  renderWithClient(<QrLoginStep accountId="qr-1" onDone={onDone} />);
  await userEvent.click(screen.getByText('Показать QR-код'));
  await userEvent.type(await screen.findByLabelText('Облачный пароль (2FA)'), 'right');
  await userEvent.click(screen.getByText('Войти'));

  await waitFor(() => {
    expect(onDone).toHaveBeenCalledTimes(1);
  });
  expect(screen.getByText('Вход выполнен')).toBeInTheDocument();
});

test('an account that is already signed in reports done once, however often it re-renders', async () => {
  const onDone = vi.fn();
  route({
    begin: () => jsonResponse(view('alive', { account: SAVED })),
    poll: () => jsonResponse(view('alive', { account: SAVED })),
  });
  const { rerender } = renderWithClient(<QrLoginStep accountId="qr-1" onDone={onDone} />);
  await userEvent.click(screen.getByText('Показать QR-код'));
  await waitFor(() => {
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  const next = vi.fn();
  rerender(
    <QueryClientProvider client={new QueryClient()}>
      <QrLoginStep accountId="qr-1" onDone={next} />
    </QueryClientProvider>,
  );

  expect(onDone).toHaveBeenCalledTimes(1);
});

test('an expired code is replaced by asking again', async () => {
  let answer = view('expired');
  route({
    begin: () => jsonResponse(answer),
    poll: () => jsonResponse(answer),
  });
  renderWithClient(<QrLoginStep accountId="qr-1" onDone={vi.fn()} />);
  await userEvent.click(screen.getByText('Показать QR-код'));
  expect(await screen.findByText('Время ожидания истекло')).toBeInTheDocument();

  answer = view('waiting', { qr_image: QR });
  await userEvent.click(screen.getByText('Показать новый код'));

  expect(await screen.findByAltText('QR-код для входа в Telegram')).toBeInTheDocument();
  expect(calls('POST')).toHaveLength(2);
});

test('a failure shows its translated reason and offers another try', async () => {
  const failed = () =>
    jsonResponse(view('failed', { error_code: 'flood_wait', flood_wait_seconds: 42 }));
  route({ begin: failed, poll: failed });
  renderWithClient(<QrLoginStep accountId="qr-1" onDone={vi.fn()} />);
  await userEvent.click(screen.getByText('Показать QR-код'));

  expect(
    await screen.findByText('Telegram просит подождать 42 с — попробуйте позже'),
  ).toBeInTheDocument();
  expect(screen.getByText('Попробовать снова')).toBeInTheDocument();
});

test('a refused request shows the code the server named', async () => {
  route({
    begin: () => jsonResponse({ error: { code: 'conflict', message: 'account_exists' } }, 409),
  });
  renderWithClient(<QrLoginStep accountId="qr-1" onDone={vi.fn()} />);
  await userEvent.click(screen.getByText('Показать QR-код'));

  expect(await screen.findByText('Этот аккаунт уже есть в Телебубе')).toBeInTheDocument();
  expect(screen.queryByText('Попробовать снова')).not.toBeInTheDocument();
});

test('an unknown refusal falls back to the generic text', async () => {
  route({
    begin: () => jsonResponse({ error: { code: 'bad_request', message: 'something_new' } }, 400),
  });
  renderWithClient(<QrLoginStep accountId="qr-1" onDone={vi.fn()} />);
  await userEvent.click(screen.getByText('Показать QR-код'));

  expect(await screen.findByText('Не удалось войти по QR-коду')).toBeInTheDocument();
  expect(screen.getByText('Попробовать снова')).toBeInTheDocument();
});
