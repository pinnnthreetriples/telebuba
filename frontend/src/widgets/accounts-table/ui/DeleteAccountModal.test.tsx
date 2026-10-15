import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { beforeEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { DeleteAccountModal } from './DeleteAccountModal';

function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(ui, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
}

function guess(reason: 'dead' | 'retired') {
  vi.mocked(fetch).mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify({ reason }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  );
}

beforeEach(() => {
  guess('retired');
});

test('confirm fires onConfirm then onClose, cancel only closes', async () => {
  const onClose = vi.fn();
  const onConfirm = vi.fn();
  renderWithClient(
    <DeleteAccountModal
      accountId="a1"
      phone="+79991234567"
      onClose={onClose}
      onConfirm={onConfirm}
    />,
  );
  expect(screen.getByText('Удалить аккаунт +79991234567?')).toBeInTheDocument();
  // The accessible name has to be the SAME sentence: dropping the interpolation
  // announced the raw "Удалить аккаунт {{phone}}?" template to a screen reader.
  expect(screen.getByRole('dialog', { name: 'Удалить аккаунт +79991234567?' })).toBeInTheDocument();

  await userEvent.click(screen.getByText('Отмена'));
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(onConfirm).not.toHaveBeenCalled();

  await userEvent.click(screen.getByText('Удалить'));
  expect(onConfirm).toHaveBeenCalledTimes(1);
  expect(onClose).toHaveBeenCalledTimes(2);
});

test('the reason is pre-selected from the server guess', async () => {
  guess('dead');
  const onConfirm = vi.fn();
  renderWithClient(
    <DeleteAccountModal accountId="a1" phone="+7" onClose={vi.fn()} onConfirm={onConfirm} />,
  );

  const select = screen.getByRole('combobox', { name: 'Причина' });
  await waitFor(() => {
    expect(select).toHaveTextContent('Умер');
  });
  const request = vi.mocked(fetch).mock.calls[0]![0] as Request;
  expect(new URL(request.url).pathname).toBe('/api/v1/accounts/a1/delete-reason');

  await userEvent.click(screen.getByText('Удалить'));
  expect(onConfirm).toHaveBeenCalledWith('dead');
});

test('the operator can override the guess', async () => {
  guess('dead');
  const onConfirm = vi.fn();
  renderWithClient(
    <DeleteAccountModal accountId="a1" phone="+7" onClose={vi.fn()} onConfirm={onConfirm} />,
  );
  const select = screen.getByRole('combobox', { name: 'Причина' });
  await waitFor(() => {
    expect(select).toHaveTextContent('Умер');
  });

  await userEvent.click(select);
  await userEvent.click(screen.getByRole('option', { name: /^Списан/ }));
  await userEvent.click(screen.getByText('Удалить'));

  expect(onConfirm).toHaveBeenCalledWith('retired');
});

test('confirming before the guess arrives leaves the reason to the server', async () => {
  vi.mocked(fetch).mockImplementation(() => new Promise<Response>(() => undefined));
  const onConfirm = vi.fn();
  renderWithClient(
    <DeleteAccountModal accountId="a1" phone="+7" onClose={vi.fn()} onConfirm={onConfirm} />,
  );

  expect(screen.getByRole('combobox', { name: 'Причина' })).toHaveTextContent('Определяем…');
  await userEvent.click(screen.getByText('Удалить'));

  expect(onConfirm).toHaveBeenCalledWith(undefined);
});
