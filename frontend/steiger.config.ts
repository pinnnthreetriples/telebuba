import { defineConfig } from 'steiger';
import fsd from '@feature-sliced/steiger-plugin';

// FSD boundary linter — the load-bearing gate per the frontend ADR. The
// generated API client under shared/api is machine-output, not an FSD slice,
// so it is excluded.
export default defineConfig([
  ...fsd.configs.recommended,
  {
    ignores: ['**/shared/api/**'],
  },
  {
    // The ADR wants the FSD skeleton from day one; early slices legitimately
    // have a single consumer until more screens land (#167+). Don't penalise
    // that structural choice.
    rules: {
      'fsd/insignificant-slice': 'off',
    },
  },
]);
