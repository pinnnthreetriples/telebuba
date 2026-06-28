import { Outlet } from '@tanstack/react-router';

import { AppNav } from './AppNav';

// The authenticated app shell: the top nav above the routed page.
export function AppShell() {
  return (
    <>
      <AppNav />
      <Outlet />
    </>
  );
}
