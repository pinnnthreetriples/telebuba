import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
} from '@tanstack/react-router';

import { AccountsPage } from '@/pages/accounts';
import { LoginPage } from '@/pages/login';
import { LogsPage } from '@/pages/logs';
import { NeurocommentPage } from '@/pages/neurocomment';
import { SettingsPage } from '@/pages/settings';
import { WarmingPage } from '@/pages/warming';
import { meQueryOptions } from '@/shared/auth';
import { queryClient } from '@/shared/lib';
import { AppShell } from '@/widgets/nav';

const rootRoute = createRootRoute({ component: Outlet });

// Public login route.
const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  component: LoginPage,
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
  component: AccountsPage,
});

const warmingRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/warming',
  component: WarmingPage,
});

const neurocommentRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/neurocomment',
  component: NeurocommentPage,
});

const logsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/logs',
  component: LogsPage,
});

const settingsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/settings',
  component: SettingsPage,
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
