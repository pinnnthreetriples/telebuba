import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
} from '@tanstack/react-router';

import { AccountsPage } from '@/pages/accounts';
import { LoginPage } from '@/pages/login';
import { NeurocommentPage } from '@/pages/neurocomment';
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

const routeTree = rootRoute.addChildren([
  loginRoute,
  protectedRoute.addChildren([indexRoute, warmingRoute, neurocommentRoute]),
]);

export const router = createRouter({ routeTree });

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
