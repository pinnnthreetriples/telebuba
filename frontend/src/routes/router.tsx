import {
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
  Outlet,
  redirect,
} from '@tanstack/react-router';

import { meQueryOptions } from '@/shared/auth';
import { isUnauthorized, queryClient } from '@/shared/lib';
import { AppShell } from '@/widgets/nav';

import { PageErrorPanel } from './PageErrorPanel';
import { SessionErrorPanel } from './SessionErrorPanel';

// Each page is code-split: a dynamic import per route so the login screen (and
// any single screen) doesn't pull the whole app's JS up front.
const rootRoute = createRootRoute({ component: Outlet });

// Public login route.
const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  component: lazyRouteComponent(() => import('@/pages/login'), 'LoginPage'),
});

// Resolve the session for the guard. Only a real "unauthorized" envelope means the
// session is gone; a 500, a dropped connection or a timeout used to redirect too,
// so a backend hiccup was indistinguishable from a logout — and /login has nothing
// to say about it. Those rethrow and land in `errorComponent` below instead.
// Exported for the guard's own test (the router itself is not unit-testable here).
export async function ensureSession(): Promise<void> {
  try {
    await queryClient.ensureQueryData(meQueryOptions());
  } catch (error) {
    if (isUnauthorized(error)) throw redirect({ to: '/login' });
    throw error;
  }
}

// Pathless layout that gates every child on a valid session + renders the nav shell.
const protectedRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: 'protected',
  beforeLoad: ensureSession,
  errorComponent: SessionErrorPanel,
  component: AppShell,
});

// Every page below carries `errorComponent` so a render error fails at the page's own
// match, inside AppShell: the nav — and "Log out" — stays around it. Without one the
// error bubbled to the protected layout's boundary, which reported it as a failed
// SESSION check, stripped the nav and offered a sign-in that landed back on the same
// crashing page. Scoped per page and NOT the router-wide `defaultErrorComponent`: that
// also covered /login and the root, whose copy would point at a nav those two matches do
// not have. They keep the boundary they had before this branch either way — the router
// wraps the whole match tree in its own `CatchBoundary` (`MatchesInner`).
const indexRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/',
  errorComponent: PageErrorPanel,
  component: lazyRouteComponent(() => import('@/pages/accounts'), 'AccountsPage'),
});

const warmingRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/warming',
  errorComponent: PageErrorPanel,
  component: lazyRouteComponent(() => import('@/pages/warming'), 'WarmingPage'),
});

const neurocommentRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/neurocomment',
  errorComponent: PageErrorPanel,
  component: lazyRouteComponent(() => import('@/pages/neurocomment'), 'NeurocommentPage'),
});

const logsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/logs',
  errorComponent: PageErrorPanel,
  component: lazyRouteComponent(() => import('@/pages/logs'), 'LogsPage'),
});

const settingsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/settings',
  errorComponent: PageErrorPanel,
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
