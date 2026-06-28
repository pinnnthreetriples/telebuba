import * as Sentry from '@sentry/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from '@tanstack/react-router';

import { router } from '@/routes';
import { queryClient } from '@/shared/lib';
import '@/shared/i18n';

export function App() {
  return (
    <Sentry.ErrorBoundary fallback={<p className="p-8">Что-то пошло не так.</p>}>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </Sentry.ErrorBoundary>
  );
}
