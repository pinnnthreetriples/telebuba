import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { CodeLoginStep } from './CodeLoginStep';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function refusal(message: string, fields?: Record<string, string>): Response {
  return json({ error: { code: 'bad_request', message, fields } }, 400);
}

// `submit` answers the confirm call; the request-code call always succeeds.
function wire(submit: () => Response) {
  vi.mocked(fetch).mockImplementation((input) => {
    const { pathname } = new URL((input as Request).url);
    if (pathname.endsWith('/request-code')) {
      return Promise.resolve(json({ account_id: 'acc-1', phone: '+79990001122' }));
    }
    if (pathname.endsWith('/submit-code')) return Promise.resolve(submit());
    return Promise.resolve(json({}));
  });
}

function calls(fragment: string): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => request.url.includes(fragment));
}

async function openCodeStep() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <CodeLoginStep accountId="acc-1" phone="+79990001122" onDone={vi.fn()} />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByText('Отправить код'));
  await screen.findByText(/Код отправлен/);
}

async function confirm(code = '12345') {
  await userEvent.type(screen.getByLabelText(/Код из SMS/), code);
  await userEvent.click(screen.getByRole('button', { name: 'Подтвердить вход' }));
}

test('the 2FA password is sent verbatim while the code is trimmed', async () => {
  wire(() => json({ account_id: 'acc-1', status: 'alive', created_at: 'n', updated_at: 'n' }));
  await openCodeStep();
  await userEvent.type(screen.getByLabelText(/2FA-пароль/), ' pw ');
  await confirm(' 12345 ');

  await waitFor(() => {
    expect(calls('/submit-code')).toHaveLength(1);
  });
  const body = JSON.parse(await calls('/submit-code')[0]!.clone().text()) as unknown;
  expect(body).toEqual({ code: '12345', password: ' pw ' });
});

test('a 2FA account asked for with only the code gets a clear ask for the password', async () => {
  wire(() => refusal('password_needed'));
  await openCodeStep();
  await confirm();

  expect(await screen.findByText(/введите облачный пароль/)).toBeInTheDocument();
  expect(screen.getByLabelText(/2FA-пароль/)).toHaveAttribute('aria-invalid', 'true');
});

test('an expired code offers a new one at once and resending asks Telegram again', async () => {
  wire(() => refusal('code_expired'));
  await openCodeStep();
  // Right after a send the resend is on cooldown.
  expect(screen.getByRole('button', { name: /Новый код через/ })).toBeDisabled();
  await confirm();

  expect(await screen.findByText(/Срок действия кода истёк/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Отправить новый код' }));
  await waitFor(() => {
    expect(calls('/request-code')).toHaveLength(2);
  });
});

test('a flood wait names the seconds and keeps the resend locked', async () => {
  wire(() => refusal('flood_wait', { retry_after_seconds: '42' }));
  await openCodeStep();
  await confirm();

  expect(await screen.findByText(/подождать 42 с/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Новый код через/ })).toBeDisabled();
});

test('a timeout and a wrong password each read as their own message', async () => {
  wire(() => refusal('unavailable'));
  await openCodeStep();
  await confirm();
  expect(await screen.findByText(/проверьте прокси/)).toBeInTheDocument();

  wire(() => refusal('password_invalid'));
  await userEvent.click(screen.getByRole('button', { name: 'Подтвердить вход' }));
  expect(await screen.findByText('Неверный облачный пароль')).toBeInTheDocument();
});
