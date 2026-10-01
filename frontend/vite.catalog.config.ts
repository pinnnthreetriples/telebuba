import { defineConfig } from 'vite';

import applicationConfig from './vite.config';

// Browser fixtures own API responses in the catalog. A missed fixture must fail
// locally rather than falling through to the running backend's dev proxy.
export default defineConfig({
  ...applicationConfig,
  server: { ...applicationConfig.server, proxy: undefined },
  plugins: [
    ...(applicationConfig.plugins ?? []),
    {
      name: 'catalog-offline-api',
      configureServer(server) {
        server.middlewares.use('/api', (_request, response) => {
          response.statusCode = 503;
          response.setHeader('Content-Type', 'application/json');
          response.end(JSON.stringify({ detail: 'Catalog requires an offline API fixture' }));
        });
      },
    },
  ],
});
