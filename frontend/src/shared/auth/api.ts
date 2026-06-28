// Auth data access, re-exported from the generated client (FSD: data only via
// shared/api). meQueryOptions resolves the current user from GET /api/v1/auth/me.
export {
  getMeOptions as meQueryOptions,
  loginMutation,
  logoutMutation,
} from '@/shared/api/@tanstack/react-query.gen';
