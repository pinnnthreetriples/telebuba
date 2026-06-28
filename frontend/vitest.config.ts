import { fileURLToPath, URL } from 'node:url';

import { defineConfig } from 'vitest/config';

// Separate from vite.config.ts so the test runner's bundled vite types don't
// clash with the dev build's @vitejs/plugin-react types. JSX is transpiled by
// esbuild (automatic runtime) — the React fast-refresh plugin isn't needed here.
export default defineConfig({
  esbuild: { jsx: 'automatic' },
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    globals: true,
    environment: 'happy-dom',
    setupFiles: ['./vitest.setup.ts'],
    css: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      all: true,
      include: ['src/**/*.{ts,tsx}'],
      // Generated client, shadcn primitives, app/route wiring, barrels and i18n
      // config are excluded per the FSD ADR (machine-output / low-signal); the
      // 80% floor applies to slice logic (pages/features/entities/lib).
      exclude: [
        'src/shared/api/**',
        'src/shared/ui/**',
        'src/shared/i18n/**',
        'src/app/**',
        'src/routes/**',
        'src/main.tsx',
        'src/**/index.ts',
        'src/**/*.gen.ts',
        '**/*.d.ts',
      ],
      thresholds: { lines: 80, functions: 80, branches: 80, statements: 80 },
    },
  },
});
