// Proxy-pool write actions through the shared API client.
export {
  assignProxyMutation,
  checkProxyMutation,
  createProxyMutation,
  deleteProxyMutation,
  probeProxyMutation,
  unassignProxyMutation,
} from '@/shared/api/@tanstack/react-query.gen';
export { assignProxyByEndpointMutation } from './proxy.assignment';
