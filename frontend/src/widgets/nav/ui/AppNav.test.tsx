import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement, ReactNode } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';
import { queryClient } from '@/shared/lib';

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

// Regression guard for an unbounded requestAnimationFrame chain. The sliding
// active-indicator measures the nav in JS and retries next frame while the active
// link's rect is 0-wide. That width is *permanent* whenever the nav generates no
// boxes — display:none below `lg` in a browser, and every element under happy-dom
// — so without the retry bound in AppNav this re-arms rAF every frame for the
// whole session (measured: ~14k frames here). The only cancel is effect cleanup.
test('does not schedule animation frames forever when the nav has no layout boxes', async () => {
  routeApi();
  const raf = vi.spyOn(window, 'requestAnimationFrame');
  renderWithClient(<AppNav />);
  await waitFor(() => {
    expect(screen.getByText('AD')).toBeInTheDocument();
  });

  // Long enough that an unbounded chain would be far into the thousands (measured:
  // ~14k in 60ms before the fix), while the bounded one drains to a couple of dozen.
  await new Promise((resolve) => {
    setTimeout(resolve, 200);
  });
  expect(raf.mock.calls.length).toBeLessThan(100);
  raf.mockRestore();
});

test('the hamburger opens a drawer with the nav destinations', async () => {
  routeApi();
  renderWithClient(<AppNav />);

  const hamburger = await screen.findByLabelText('Меню');
  expect(hamburger).toHaveAttribute('aria-expanded', 'false');

  await userEvent.click(hamburger);
  const drawer = await screen.findByRole('dialog');
  expect(hamburger).toHaveAttribute('aria-expanded', 'true');
  expect(drawer).toHaveTextContent('Аккаунты');
  expect(drawer).toHaveTextContent('Настройки');

  await userEvent.click(screen.getByLabelText('Закрыть меню'));
  await waitFor(() => {
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

test('clears the query cache on logout so authed data cannot leak on back-nav', async () => {
  navigate.mockClear();
  const clearSpy = vi.spyOn(queryClient, 'clear');
  routeApi();
  renderWithClient(<AppNav />);
  await waitFor(() => {
    expect(screen.getByText('AD')).toBeInTheDocument();
  });

  await userEvent.click(screen.getByLabelText('Аккаунт'));
  await userEvent.click(screen.getByText('Выйти'));

  await waitFor(() => {
    expect(clearSpy).toHaveBeenCalled();
  });
  clearSpy.mockRestore();
});
