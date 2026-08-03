import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from '@tanstack/react-router';
import { render, screen, waitFor } from '@testing-library/react';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import { PageErrorPanel } from './PageErrorPanel';
import { router } from './router';

// The panel's copy sends the operator to the nav, so it belongs on the routes that have
// one: every child of the protected layout, which renders AppShell around them.
test('every page inside the nav shell carries the page-error boundary', () => {
  const pages = [
    '/protected/',
    '/protected/warming',
    '/protected/neurocomment',
    '/protected/logs',
    '/protected/settings',
  ] as const;
  for (const id of pages) {
    expect(router.routesById[id].options.errorComponent).toBe(PageErrorPanel);
  }
});

test('a crash outside the nav shell is not dressed up as a page error', async () => {
  // The public part of the tree, wired as `router.tsx` wires it: no boundary on the root
  // or the login route, and the real router's `defaultErrorComponent` — which must stay
  // unset. A router-wide default reached these two matches as well, and neither has a
  // nav: the panel told the operator to switch sections through a menu that is not on
  // the screen, and offered no other control at all. Unset, the error passes the login
  // match and is handled above it by the router's own top-level boundary, exactly as it
  // was before this branch.
  let crashes = 0;
  const rootRoute = createRootRoute({ component: Outlet });
  const loginRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/login',
    component: (): never => {
      crashes += 1;
      throw new TypeError('сломанный экран входа');
    },
  });
  const standIn = createRouter({
    routeTree: rootRoute.addChildren([loginRoute]),
    history: createMemoryHistory({ initialEntries: ['/login'] }),
    defaultErrorComponent: router.options.defaultErrorComponent,
  });

  render(<RouterProvider router={standIn} />);

  await waitFor(() => {
    expect(crashes).toBeGreaterThan(0);
  });
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.queryByText(/Перейдите в другой раздел/)).not.toBeInTheDocument();
});
