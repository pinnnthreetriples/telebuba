import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement, ReactNode } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

const navigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
  useRouterState: () => '/',
  useNavigate: () => navigate,
}));

// Imported after the mock so AppNav picks up the stubbed router primitives.
const { AppNav } = await import('./AppNav');

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

function routeApi() {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = new URL((input as Request).url);
    if (url.pathname === '/api/v1/auth/me') {
      return Promise.resolve(jsonResponse({ id: 'u1', username: 'admin' }));
    }
    return Promise.resolve(jsonResponse({}));
  });
}

test('shows real initials from the current user', async () => {
  routeApi();
  renderWithClient(<AppNav />);
  await waitFor(() => {
    expect(screen.getByText('AD')).toBeInTheDocument();
  });
});

interface MockSourceCtor {
  last(): { emitOpen(): void; emitError(): void } | undefined;
}
const Sources = globalThis.EventSource as unknown as MockSourceCtor;

test('the system pill reflects the real SSE connection state', async () => {
  routeApi();
  renderWithClient(<AppNav />);
  expect(screen.getByText('Нет соединения')).toBeInTheDocument();

  act(() => {
    Sources.last()?.emitOpen();
  });
  expect(await screen.findByText('Система активна')).toBeInTheDocument();

  act(() => {
    Sources.last()?.emitError();
  });
  expect(await screen.findByText('Нет соединения')).toBeInTheDocument();
});

test('logs out from the avatar menu and redirects to login', async () => {
  navigate.mockClear();
  routeApi();
  renderWithClient(<AppNav />);
  await waitFor(() => {
    expect(screen.getByText('AD')).toBeInTheDocument();
  });

  await userEvent.click(screen.getByLabelText('Аккаунт'));
  await userEvent.click(screen.getByText('Выйти'));

  await waitFor(() => {
    const loggedOut = vi
      .mocked(fetch)
      .mock.calls.some(([input]) => (input as Request).url.includes('/auth/logout'));
    expect(loggedOut).toBe(true);
  });
  await waitFor(() => {
    expect(navigate).toHaveBeenCalledWith({ to: '/login' });
  });
});
