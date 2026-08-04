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
import { AppShell } from '@/widgets/nav';

import { PageErrorPanel } from './PageErrorPanel';
import { router } from './router';
import { SessionErrorPanel } from './SessionErrorPanel';

// Every position in the tree gets the boundary it is supposed to have, and no other:
// pages inside the nav shell get the page panel, the shell itself owns the SESSION
// panel, and everything with no nav around it — /login, the root — gets none, because a
// login crash dressed as a failed session check is the exact confusion these panels
// exist to keep apart. Read off the real route tree, not a list of ids: a frozen list
// keeps passing while a page added next to them ships with no boundary at all, and it
// fails on a rename that moved nothing.
test('every page inside the nav shell carries the page-error boundary', () => {
  const routes = Object.values(router.routesById);
  const shell = routes.filter((route) => route.options.component === AppShell);
  // By id prefix, so a page nested deeper than a direct child is covered too.
  const inShell = routes.filter((route) =>
    shell.some((layout) => route.id.startsWith(`${layout.id}/`)),
  );

  // Guard the derivation itself: an empty match would make the loops below vacuous.
  expect(shell).toHaveLength(1);
  expect(inShell.length).toBeGreaterThan(1);
  for (const layout of shell) {
    expect(layout.options.errorComponent, `${layout.id} is the session boundary`).toBe(
      SessionErrorPanel,
    );
  }
  for (const page of inShell) {
    expect(page.options.errorComponent, `${page.id} has no page boundary`).toBe(PageErrorPanel);
  }
  for (const outside of routes.filter(
    (route) => !inShell.includes(route) && !shell.includes(route),
  )) {
    expect(outside.options.errorComponent, `${outside.id} has no nav around it`).toBeUndefined();
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
