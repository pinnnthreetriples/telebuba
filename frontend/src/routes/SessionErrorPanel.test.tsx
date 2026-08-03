import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { SessionErrorPanel } from './SessionErrorPanel';

// This boundary replaces AppShell, so while it shows there is no nav and no way to
// log out. A lone "reload the page" sentence loops forever against a backend that
// stays down — the panel needs a retry that re-runs the guard, and a way to /login.
// `Link` needs a router in context, so the panel is mounted as one route's component.
test('the session-error panel offers a retry and a way to the login screen', async () => {
  const reset = vi.fn();
  const routeTree = createRootRoute({ component: () => <SessionErrorPanel reset={reset} /> });
  const panelRouter = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ['/'] }),
  });
  render(<RouterProvider router={panelRouter} />);

  await waitFor(() => {
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });
  await userEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  expect(reset).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('link', { name: 'Перейти ко входу' })).toHaveAttribute('href', '/login');
});
