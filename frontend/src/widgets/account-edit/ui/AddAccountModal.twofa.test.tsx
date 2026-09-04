// The wizard's LAST step wired into the wizard: which step number the cloud
// password is for each method, how the batch is reached from the proxy step and
// from the code step, what the operator recognises each account by there, and the
// dialog title over the finished passwords. The step's own behaviour is in
// TwoFactorBulkStep.test.tsx / TwoFactorBulkResults.test.tsx.

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

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function account(id: string) {
  return { account_id: id, status: 'new', created_at: 'n', updated_at: 'n' };
}

// Every route the wizard walks, plus the 2FA POST its last step sends. Thunks,
// never Responses — a body can only be read once and the batch reuses the route.
function routeApi() {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname.endsWith('/2fa')) {
      return jsonResponse({ password: `test-password-${pathname.split('/')[4] ?? ''}` });
    }
    if (pathname === '/api/v1/accounts/import-session') {
      const file = (await request.formData()).get('file') as File;
      return jsonResponse(account(file.name.replace('.session', '')));
    }
    if (pathname === '/api/v1/accounts/start-login') {
      return jsonResponse({ ...account('79990001122'), phone: '+79990001122' });
    }
    if (pathname.endsWith('/request-code')) {
      return jsonResponse({ account_id: '79990001122', phone: '+79990001122' });
    }
    if (pathname.endsWith('/submit-code')) return jsonResponse(account('79990001122'));
    return jsonResponse({ items: [], next_cursor: null });
  });
}

function pickSessions(...names: string[]): void {
  fireEvent.change(document.body.querySelector('input[type="file"]') as HTMLInputElement, {
    target: {
      files: names.map((name) => new File(['x'], name, { type: 'application/octet-stream' })),
    },
  });
}

// Step 1 → step 2 for the file methods, with the whole batch imported.
async function importAndAdvance(...names: string[]): Promise<void> {
  await userEvent.click(screen.getByText('Файл .session'));
  pickSessions(...names);
  await waitFor(() => {
    expect(screen.getByText('Далее')).toBeEnabled();
  });
  await userEvent.click(screen.getByText('Далее'));
}

test('a file import ends on the cloud password, naming each account by its file', async () => {
  routeApi();
  const onClose = vi.fn();
  renderWithClient(<AddAccountModal onClose={onClose} onImported={vi.fn()} />);
  // Three steps for a file method, and the cloud password is the last of them.
  expect(screen.queryByText('4')).not.toBeInTheDocument();
  await importAndAdvance('a.session', 'b.session');
  await userEvent.click(screen.getByText('Пропустить'));

  expect(await screen.findByText('Шаг 3 · облачный пароль')).toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
  // Nothing has connected as these accounts yet, so the file they came out of is
  // all the operator has to tell them apart by.
  expect(screen.getByText('a.session')).toBeInTheDocument();
  expect(screen.getByText('b.session')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Включить для 2 аккаунтов' })).toBeEnabled();
});

test('the phone method spends four steps and hands the code step over to the batch', async () => {
  routeApi();
  const onClose = vi.fn();
  renderWithClient(<AddAccountModal onClose={onClose} onImported={vi.fn()} />);

  await userEvent.click(screen.getByText('Номер телефона'));
  await userEvent.type(screen.getByPlaceholderText('+7 999 000-11-22'), '+79990001122');
  await userEvent.click(screen.getByText('Продолжить'));
  // The login code costs the phone method a step the file methods do not pay.
  expect(await screen.findByText('4')).toBeInTheDocument();
  await waitFor(() => {
    expect(screen.getByText('Далее')).toBeEnabled();
  });
  await userEvent.click(screen.getByText('Далее'));
  await userEvent.click(screen.getByText('Пропустить'));

  expect(screen.getByText('Шаг 3 · вход по коду')).toBeInTheDocument();
  await userEvent.click(screen.getByText('Отправить код'));
  await userEvent.type(await screen.findByLabelText('Код из SMS'), '11111');
  await userEvent.click(screen.getByText('Подтвердить вход'));

  expect(await screen.findByText('Шаг 4 · облачный пароль')).toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByText('+79990001122')).toBeInTheDocument();
});

test('the dialog title flips to «Пароли созданы» once the passwords are on screen', async () => {
  routeApi();
  renderWithClient(<AddAccountModal onClose={vi.fn()} onImported={vi.fn()} />);
  await importAndAdvance('a.session');
  await userEvent.click(screen.getByText('Пропустить'));
  await userEvent.click(await screen.findByRole('button', { name: 'Включить для 1 аккаунта' }));

  expect(await screen.findByText('test-password-a')).toBeInTheDocument();
  // "Добавить аккаунт" over the passwords is a title for a wizard that is
  // already finished.
  expect(screen.getByText('Пароли созданы')).toBeInTheDocument();
  expect(screen.queryByText('Добавить аккаунт')).not.toBeInTheDocument();
});
