import { QueryCache, QueryClient } from '@tanstack/react-query';

// On a 401 the generated client throws our error envelope {error:{code,...}};
// "unauthorized" means the session is gone, so send the user to /login.
function isUnauthorized(error: unknown): boolean {
  if (typeof error !== 'object' || error === null || !('error' in error)) return false;
  const detail = (error as { error: unknown }).error;
  return (
    typeof detail === 'object' &&
    detail !== null &&
    (detail as { code?: unknown }).code === 'unauthorized'
  );
}

export const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (error) => {
      if (isUnauthorized(error) && window.location.pathname !== '/login') {
        window.location.assign('/login');
      }
    },
  }),
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});
