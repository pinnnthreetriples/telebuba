import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';
import prettier from 'eslint-config-prettier';

import designTokens from './eslint-rules/design-tokens.js';

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
      // Local rule, in eslint-rules/: it is the gate that keeps the design system a
      // closed set, and its reasons live next to it.
      'design-tokens': designTokens,
    },
    rules: {
      // The two classic hook rules, named rather than spread from
      // ``reactHooks.configs.recommended``: since plugin v6 that preset also turns on
      // the fifteen React Compiler rules (purity, refs, set-state-in-effect, …). The
      // plugin was bumped to v7 because it is the first line that peers on ESLint 10
      // (needed for the brace-expansion advisory), NOT to adopt a new ruleset — those
      // rules flag pre-existing app code and are their own reviewed change.
      'design-tokens/no-raw-values': 'error',
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // One useMutation is ONE callback slot and ONE result: calling .mutate()
      // once per item of a list drives N requests through it, so N-1 outcomes
      // are unobservable and N-1 onSettled handlers never run. That exact shape
      // shipped twice on this branch (a story published twice, and a new
      // campaign whose channels were linked but never re-read).
      //
      // Deliberately narrow. The broad version of this rule — flag every
      // `.mutate(vars, { onSuccess })` and every `.reset()` — flags 51 and 12
      // existing sites respectively, across 23 files, and most are correct:
      // a modal with one submit button or a single global start/stop has no
      // second concurrent caller to take the slot. A syntactic rule cannot tell
      // "per row on a shared hook" from "the only caller", so the broad form
      // would have to be suppressed nearly everywhere, which buys less than
      // nothing. A mutation inside a loop is unambiguous, and costs no
      // suppressions: it flags zero existing sites.
      'no-restricted-syntax': [
        'error',
        {
          selector:
            "CallExpression[callee.property.name=/^(forEach|map|flatMap)$/] CallExpression[callee.property.name='mutate']",
          message:
            'One useMutation cannot carry N concurrent calls: its result and callbacks are a single slot. Use mutateAsync and await the batch (Promise.all/allSettled), handling each result.',
        },
      ],
    },
  },
  {
    // The rule's own fixtures are the patterns it bans, so it cannot lint them: a
    // gate that flags nothing looks identical to a gate that catches nothing, and
    // this is the file that tells them apart.
    files: ['**/designTokenRule.test.ts'],
    rules: { 'design-tokens/no-raw-values': 'off' },
  },
  prettier,
);
