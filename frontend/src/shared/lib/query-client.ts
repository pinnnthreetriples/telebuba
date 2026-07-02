import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query';

import { i18n } from '@/shared/i18n';
import { toastError } from '@/shared/ui';

// The generated client throws our error envelope {error:{code,message,fields?}}.
interface ErrorEnvelope {
  error: { code?: string; message?: string };
}

function asEnvelope(error: unknown): ErrorEnvelope['error'] | null {
  if (typeof error !== 'object' || error === null || !('error' in error)) return null;
  const detail = (error as { error: unknown }).error;
  if (typeof detail !== 'object' || detail === null) return null;
  return detail as ErrorEnvelope['error'];
}

// "unauthorized" means the session is gone, so send the user to /login.
function isUnauthorized(error: unknown): boolean {
  return asEnvelope(error)?.code === 'unauthorized';
}

export const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (error) => {
      if (isUnauthorized(error) && window.location.pathname !== '/login') {
        window.location.assign('/login');
      }
    },
  }),
  // Mutations don't surface errors on their own — show the API envelope's
  // message (or a translated fallback) so failures aren't silently swallowed.
  mutationCache: new MutationCache({
    onError: (error) => {
      if (isUnauthorized(error)) return; // the query cache already redirects
      const detail = asEnvelope(error);
      toastError(detail?.message ?? i18n.t('shell.mutationError'));
    },
  }),
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});
