import * as Sentry from '@sentry/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from '@tanstack/react-router';

import { router } from '@/routes';
import { i18n } from '@/shared/i18n';
import { queryClient } from '@/shared/lib';
import { Toaster } from '@/shared/ui';
import '@/shared/i18n';

export function App() {
  return (
    <Sentry.ErrorBoundary fallback={<p className="p-8">{i18n.t('shell.fatalError')}</p>}>
      <QueryClientProvider client={queryClient}>
        <div className="min-h-screen bg-white">
          <RouterProvider router={router} />
        </div>
        <Toaster />
      </QueryClientProvider>
    </Sentry.ErrorBoundary>
  );
}
