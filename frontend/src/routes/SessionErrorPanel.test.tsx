import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from '@tanstack/react-router';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import { router } from './router';
import { SessionErrorPanel } from './SessionErrorPanel';

// A stand-in for the protected layout, wired as `router.tsx` wires it: the same boundary
// component over a pathless route whose guard can fail, one child page, and the REAL
// router's `defaultErrorComponent` — so dropping it there breaks the second test.
// A router of our own is the only way to drive a guard failure and count the retries; a
// `reset = vi.fn()` mock could only pin the wiring, which is how the panel shipped with a
// Retry button that did nothing.
function buildRouter(guard: (() => void) | undefined, page: () => ReactElement) {
  const rootRoute = createRootRoute({ component: Outlet });
  const protectedRoute = createRoute({
    getParentRoute: () => rootRoute,
    id: 'protected',
    beforeLoad: guard,
    errorComponent: SessionErrorPanel,
    component: () => (
      <>
        <nav aria-label="Меню" />
        <Outlet />
      </>
    ),
  });
  const pageRoute = createRoute({
    getParentRoute: () => protectedRoute,
    path: '/',
    component: page,
  });
  return createRouter({
    routeTree: rootRoute.addChildren([protectedRoute.addChildren([pageRoute])]),
    history: createMemoryHistory({ initialEntries: ['/'] }),
    defaultErrorComponent: router.options.defaultErrorComponent,
  });
}

test('the session-error panel retries by re-running the guard, and links to sign-in', async () => {
  let guardCalls = 0;
  render(
    <RouterProvider
      router={buildRouter(
        () => {
          guardCalls += 1;
          throw new Error('session check failed');
        },
        () => (
          <div>страница</div>
        ),
      )}
    />,
  );

  await waitFor(() => {
    expect(screen.getByRole('alert')).toHaveTextContent('Не удалось проверить сессию');
  });
  expect(guardCalls).toBe(1);

  await userEvent.click(screen.getByRole('button', { name: 'Повторить' }));

  // The boundary's own `reset` only clears the boundary: the re-mounted match reads the
  // same stored error and throws it straight back, so the count stayed at 1 and the only
  // observable effect of the button was a second console error. `invalidate` re-resolves.
  await waitFor(() => {
    expect(guardCalls).toBe(2);
  });
  // The panel replaces AppShell, so this link is the only way out while it shows.
  expect(screen.getByRole('link', { name: 'Перейти ко входу' })).toHaveAttribute('href', '/login');
});

test('a crashing page is reported as a page error, not as a failed session check', async () => {
  render(
    <RouterProvider
      router={buildRouter(undefined, () => {
        throw new TypeError('одна плохая строка');
      })}
    />,
  );

  await waitFor(() => {
    expect(screen.getByRole('alert')).toHaveTextContent('На этой странице что-то пошло не так');
  });
  // With no boundary of its own the page's error bubbled to the protected layout's, which
  // claimed the SESSION could not be verified, replaced the nav, and offered a sign-in
  // that navigates back to the same crashing page.
  expect(screen.queryByText(/Не удалось проверить сессию/)).not.toBeInTheDocument();
  expect(screen.getByRole('navigation')).toBeInTheDocument();
});
