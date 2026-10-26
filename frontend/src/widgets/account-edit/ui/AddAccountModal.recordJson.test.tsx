import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { AddAccountModal } from './AddAccountModal';

// The seller JSON's password is recorded AFTER the proxy step (the import never
// talks to Telegram) and BEFORE the second-device step, which signs in with it.

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

const account = (id: string) => ({
  account_id: id,
  status: 'new',
  created_at: 'n',
  updated_at: 'n',
});

function routeApi(recordAnswer: () => Response) {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/import-session') {
      const form = await request.formData();
      return jsonResponse(account((form.get('file') as File).name.replace('.session', '')));
    }
    if (pathname.endsWith('/2fa/record-json')) return recordAnswer();
    return jsonResponse({ items: [], next_cursor: null });
  });
}

function calls(fragment: string): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input as Request)
    .filter((request) => request.url.includes(fragment));
}

function pick(...names: string[]): void {
  fireEvent.change(document.body.querySelector('input[type="file"]') as HTMLInputElement, {
    target: { files: names.map((name) => new File(['x'], name)) },
  });
}

async function importPairAndReachProxy(devices?: string): Promise<void> {
  await userEvent.click(screen.getByText('Файл .session'));
  if (devices) {
    await userEvent.click(
      within(screen.getByRole('radiogroup', { name: 'Устройство' })).getByRole('radio', {
        name: devices,
      }),
    );
  }
  pick('a.session', 'a.json');
  await waitFor(() => {
    expect(screen.getByText('Далее')).toBeEnabled();
  });
  await userEvent.click(screen.getByText('Далее'));
}

test('the JSON password is recorded after the proxy step and before the device step', async () => {
  routeApi(() => jsonResponse({ has_stored_password: true }));
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);
  await importPairAndReachProxy('Оба');

  // Importing never reaches Telegram, and neither does arriving at the proxy step.
  expect(calls('/2fa/record-json')).toHaveLength(0);
  await userEvent.click(screen.getByText('Пропустить'));

  expect(await screen.findByText('Шаг 3 · второе устройство')).toBeInTheDocument();
  const [request] = calls('/2fa/record-json');
  expect(new URL(request?.url ?? '').pathname).toBe('/api/v1/accounts/a/2fa/record-json');
  expect(((await request?.clone().formData())?.get('json_file') as File).name).toBe('a.json');
  expect(calls('/devices')).toHaveLength(0);

  await userEvent.click(screen.getByText('Войти как «Телефон»'));
  await userEvent.click(await screen.findByText('Далее'));
  expect(await screen.findByText('пароль сохранён')).toBeInTheDocument();
});

test('a refused JSON password is shown with its reason and the account is not skipped', async () => {
  routeApi(() =>
    jsonResponse(
      { error: { code: 'bad_request', message: 'twofa_current_password_invalid' } },
      400,
    ),
  );
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);
  await importPairAndReachProxy();

  await userEvent.click(screen.getByText('Пропустить'));

  expect(await screen.findByText('Шаг 3 · облачный пароль')).toBeInTheDocument();
  expect(screen.getByText(/пароль из JSON не сохранён/)).toBeInTheDocument();
  expect(screen.queryByText('пароль сохранён')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Включить для 1 аккаунта' })).toBeEnabled();
});

test('a session without a JSON records nothing', async () => {
  routeApi(() => jsonResponse({}));
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);
  await userEvent.click(screen.getByText('Файл .session'));
  pick('a.session');
  await waitFor(() => {
    expect(screen.getByText('Далее')).toBeEnabled();
  });
  await userEvent.click(screen.getByText('Далее'));
  await userEvent.click(screen.getByText('Пропустить'));

  expect(await screen.findByText('Шаг 3 · облачный пароль')).toBeInTheDocument();
  expect(calls('/2fa/record-json')).toHaveLength(0);
});
