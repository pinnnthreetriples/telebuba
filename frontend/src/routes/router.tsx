import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
} from '@tanstack/react-router';

import { AccountsPage } from '@/pages/accounts';
import { LoginPage } from '@/pages/login';
import { meQueryOptions } from '@/shared/auth';
import { queryClient } from '@/shared/lib';

const rootRoute = createRootRoute({ component: Outlet });

// Public login route.
const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  component: LoginPage,
});

// Pathless layout that gates every child on a valid session.
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
  component: Outlet,
});

const indexRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/',
  component: AccountsPage,
});

const routeTree = rootRoute.addChildren([loginRoute, protectedRoute.addChildren([indexRoute])]);

export const router = createRouter({ routeTree });

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
