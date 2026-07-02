import {
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
  Outlet,
  redirect,
} from '@tanstack/react-router';

import { meQueryOptions } from '@/shared/auth';
import { queryClient } from '@/shared/lib';
import { AppShell } from '@/widgets/nav';

// Each page is code-split: a dynamic import per route so the login screen (and
// any single screen) doesn't pull the whole app's JS up front.
const rootRoute = createRootRoute({ component: Outlet });

// Public login route.
const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  component: lazyRouteComponent(() => import('@/pages/login'), 'LoginPage'),
});

// Pathless layout that gates every child on a valid session + renders the nav shell.
const protectedRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: 'protected',
  beforeLoad: async () => {
    try {
      await queryClient.ensureQueryData(meQueryOptions());
    } catch {
      throw redirect({ to: '/login' });
    }
  },
  component: AppShell,
});

const indexRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/',
  component: lazyRouteComponent(() => import('@/pages/accounts'), 'AccountsPage'),
});

const warmingRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/warming',
  component: lazyRouteComponent(() => import('@/pages/warming'), 'WarmingPage'),
});

const neurocommentRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/neurocomment',
  component: lazyRouteComponent(() => import('@/pages/neurocomment'), 'NeurocommentPage'),
});

const logsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/logs',
  component: lazyRouteComponent(() => import('@/pages/logs'), 'LogsPage'),
});

const settingsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/settings',
  component: lazyRouteComponent(() => import('@/pages/settings'), 'SettingsPage'),
});

const routeTree = rootRoute.addChildren([
  loginRoute,
  protectedRoute.addChildren([
    indexRoute,
    warmingRoute,
    neurocommentRoute,
    logsRoute,
    settingsRoute,
  ]),
]);

export const router = createRouter({ routeTree });

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
