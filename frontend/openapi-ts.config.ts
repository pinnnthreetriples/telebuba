import { defineConfig } from '@hey-api/openapi-ts';

// Generates the typed client + TanStack Query options into shared/api (FSD: the
// only data-access seam). Regenerated from the backend OpenAPI by `npm run gen:api`
// and drift-checked in CI — never hand-edit the output.
export default defineConfig({
  input: './openapi.json',
  output: './src/shared/api',
  plugins: ['@hey-api/client-fetch', '@tanstack/react-query'],
});
