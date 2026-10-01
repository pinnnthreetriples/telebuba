import type { StorybookConfig } from '@storybook/react-vite';

const config: StorybookConfig = {
  framework: '@storybook/react-vite',
  stories: ['../stories/**/*.stories.@(ts|tsx)'],
  addons: ['@storybook/addon-docs', '@storybook/addon-mcp'],
  features: { componentsManifest: true },
  viteFinal: (viteConfig) => {
    // Storybook must never inherit the application's localhost:8080 API proxy.
    viteConfig.server = { ...viteConfig.server, proxy: undefined };
    viteConfig.plugins = [
      ...(viteConfig.plugins ?? []),
      {
        name: 'storybook-offline-api',
        configureServer(server) {
          server.middlewares.use((request, response, next) => {
            if (!request.url?.startsWith('/api/')) return next();
            response.statusCode = 503;
            response.setHeader('Content-Type', 'application/json');
            response.end(
              JSON.stringify({
                error: {
                  code: 'storybook_offline',
                  message: 'Storybook has no backend connection',
                },
              }),
            );
          });
        },
      },
    ];
    return viteConfig;
  },
};

export default config;
