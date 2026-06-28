import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

const navigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}));

// Imported after the mock so LoginPage picks up the stubbed useNavigate.
const { LoginPage } = await import('./LoginPage');

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

test('logs in and navigates home on success', async () => {
  navigate.mockClear();
  vi.mocked(fetch).mockResolvedValue(jsonResponse({ id: 'u1', username: 'admin', role: 'admin' }));

  renderWithClient(<LoginPage />);
  await userEvent.type(screen.getByLabelText('Имя пользователя'), 'admin');
  await userEvent.type(screen.getByLabelText('Пароль'), 'pw');
  await userEvent.click(screen.getByRole('button', { name: 'Войти' }));

  await waitFor(() => {
    expect(navigate).toHaveBeenCalledWith({ to: '/' });
  });
});

test('shows an error on invalid credentials', async () => {
  navigate.mockClear();
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({ error: { code: 'unauthorized', message: 'invalid credentials' } }, 401),
  );

  renderWithClient(<LoginPage />);
  await userEvent.type(screen.getByLabelText('Имя пользователя'), 'admin');
  await userEvent.type(screen.getByLabelText('Пароль'), 'bad');
  await userEvent.click(screen.getByRole('button', { name: 'Войти' }));

  await waitFor(() => {
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });
  expect(navigate).not.toHaveBeenCalled();
});
