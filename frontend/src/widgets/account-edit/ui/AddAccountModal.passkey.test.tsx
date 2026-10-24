// The passkey step wired into the add wizard: it follows the cloud password only
// when the key vault is configured. The step's own batch is PasskeyBulkStep.test.tsx.

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { AddAccountModal } from './AddAccountModal';

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function routeApi(vaultConfigured: boolean) {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const { pathname } = new URL(request.url);
    if (pathname === '/api/v1/accounts/import-session') {
      const file = (await request.formData()).get('file') as File;
      const id = file.name.replace('.session', '');
      return jsonResponse({ account_id: id, status: 'new', created_at: 'n', updated_at: 'n' });
    }
    if (pathname.endsWith('/passkey')) {
      return jsonResponse({
        account_id: 'a',
        has_passkey: false,
        vault_configured: vaultConfigured,
      });
    }
    return jsonResponse({ items: [], next_cursor: null });
  });
}

async function importAndSkipToTwofa(): Promise<void> {
  await userEvent.click(screen.getByText('Файл .session'));
  fireEvent.change(document.body.querySelector('input[type="file"]') as HTMLInputElement, {
    target: { files: [new File(['x'], 'a.session', { type: 'application/octet-stream' })] },
  });
  await waitFor(() => {
    expect(screen.getByText('Далее')).toBeEnabled();
  });
  await userEvent.click(screen.getByText('Далее'));
  await userEvent.click(screen.getByText('Пропустить'));
  await screen.findByText('Шаг 3 · облачный пароль');
}

test('with the vault configured the cloud password hands over to the passkey step', async () => {
  routeApi(true);
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AddAccountModal onClose={onClose} onImported={vi.fn()} />
    </QueryClientProvider>,
  );
  await importAndSkipToTwofa();
  expect(screen.getByText('4')).toBeInTheDocument();

  await userEvent.click(screen.getByText('Пропустить'));

  expect(await screen.findByText('Шаг 4 · ключ доступа')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Создать ключ для 1 аккаунта' })).toBeEnabled();
  expect(onClose).not.toHaveBeenCalled();
  await userEvent.click(screen.getByText('Пропустить'));
  expect(onClose).toHaveBeenCalled();
});

test('without a vault the cloud password stays the last step', async () => {
  routeApi(false);
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AddAccountModal onClose={onClose} onImported={vi.fn()} />
    </QueryClientProvider>,
  );
  await importAndSkipToTwofa();
  expect(screen.queryByText('4')).not.toBeInTheDocument();

  await userEvent.click(screen.getByText('Пропустить'));

  expect(onClose).toHaveBeenCalled();
  expect(screen.queryByText(/ключ доступа/)).not.toBeInTheDocument();
});
