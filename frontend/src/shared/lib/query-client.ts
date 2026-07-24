import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query';

import { i18n } from '@/shared/i18n';
import { toastError } from '@/shared/ui';

// The generated client throws our error envelope {error:{code,message,fields?}}.
interface ErrorEnvelope {
  error: { code?: string; message?: string; fields?: Record<string, unknown> };
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

// A dead session sends the user to /login — from either cache, guarding against
// a redirect loop when we're already on the login page.
function redirectToLogin(): void {
  if (window.location.pathname !== '/login') {
    window.location.assign('/login');
  }
}

// The envelope's `message` usually carries a stable locale-neutral code
// (profile_photo_stale_reference, flood_wait, channel_username_occupied, …).
// For media mutations this toast is the only place the operator sees the
// failure, so translate via the profile/channel code tables; an unknown code
// (or free-form message) shows as-is, and no envelope at all falls back to the
// generic copy. flood_wait interpolates retry_after_seconds — the backend
// serialises envelope fields as strings, so parse rather than expect a number.
function mutationErrorText(error: unknown): string {
  const detail = asEnvelope(error);
  const message = detail?.message;
  if (typeof message !== 'string' || !message.trim()) return i18n.t('shell.mutationError');
  const seconds = Number(detail?.fields?.retry_after_seconds ?? NaN);
  return i18n.t([`accounts.profile.code.${message}`, `accounts.channel.code.${message}`], {
    defaultValue: message,
    s: Number.isFinite(seconds) ? seconds : '?',
  });
}

export const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (error) => {
      if (isUnauthorized(error)) redirectToLogin();
    },
  }),
  // Mutations don't surface errors on their own — show the API envelope's
  // message (translated when it's a stable code) so failures aren't silently
  // swallowed.
  mutationCache: new MutationCache({
    onError: (error) => {
      // A mutation-only 401 must redirect too — nothing else catches it.
      if (isUnauthorized(error)) {
        redirectToLogin();
        return;
      }
      toastError(mutationErrorText(error));
    },
  }),
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});
