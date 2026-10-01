import { pageFrame } from '@/shared/design-system';
import { Outlet } from '@tanstack/react-router';

import { AppNav } from './AppNav';

// The authenticated app shell: the design's sticky top bar above the routed
// page, wrapped in the design's centered 1340px content column.
export function AppShell() {
  return (
    <>
      <AppNav />
      <main className={pageFrame('app')}>
        <Outlet />
      </main>
    </>
  );
}
