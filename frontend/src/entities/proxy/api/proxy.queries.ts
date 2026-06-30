// The proxy-pool entity's data-access surface. Wraps the generated TanStack
// Query options from shared/api (the only data seam, per the FSD ADR) so pages
// and widgets depend on the entity, not on the generated client's internals.
export { listProxiesOptions as proxyPoolQueryOptions } from '@/shared/api/@tanstack/react-query.gen';
