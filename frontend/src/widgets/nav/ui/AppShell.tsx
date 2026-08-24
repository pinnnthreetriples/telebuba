import { Outlet } from '@tanstack/react-router';

import { AppNav } from './AppNav';

// The authenticated app shell: the design's sticky top bar above the routed
// page, wrapped in the design's centered 1340px content column.
export function AppShell() {
  return (
    <>
      <AppNav />
      <main className="mx-auto max-w-shell px-lg pb-[80px] pt-2xl lg:px-2xl">
        <Outlet />
      </main>
    </>
  );
}
