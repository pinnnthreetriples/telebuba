import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';
import prettier from 'eslint-config-prettier';

export default tseslint.config(
  // Generated client, build output, coverage, and config files are out of scope.
  {
    ignores: ['dist', 'coverage', 'src/shared/api/**', '*.config.{js,ts}', 'vitest.setup.ts'],
  },
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: { globals: globals.browser },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      // The two classic hook rules, named rather than spread from
      // ``reactHooks.configs.recommended``: since plugin v6 that preset also turns on
      // the fifteen React Compiler rules (purity, refs, set-state-in-effect, …). The
      // plugin was bumped to v7 because it is the first line that peers on ESLint 10
      // (needed for the brace-expansion advisory), NOT to adopt a new ruleset — those
      // rules flag pre-existing app code and are their own reviewed change.
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    },
  },
  prettier,
);
