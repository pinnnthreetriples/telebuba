---
last_updated: 2026-07-25
---

# Frontend Rules
React 19 + strict TypeScript + Vite. Server I/O uses the generated `shared/api` client with TanStack Query; never call URLs directly or hand-edit generated files.

FSD order: `app → routes → pages → widgets → features → entities → shared`. Import only lower layers and cross slice boundaries through `index.ts`.

- Routes/pages compose; features own interactions; entities own business nouns; `shared` owns generic API/UI/lib/config/i18n.
- No `any` or ignored type failures without a precise upstream justification.
- All display strings and formatting use react-i18next/`Intl` (`ru`, `en`).
- Reuse Tailwind tokens and local `shared/ui`; do not duplicate backend policy.
- Account display identity is per-surface: reuse `entities/account` helpers (`accountDisplayName`, `accountInitials`, `<AccountAvatar>`); any account-bearing payload (e.g. warming cards) must carry `first_name`/`last_name`/`phone`/`avatar_etag`, not just a label.
- Frontend configuration uses `VITE_*`.
- Vitest logic coverage stays at least 80%; Steiger, ESLint, Prettier, TypeScript, tests and build must pass. CVEs are a separate CI job (`npm-audit`), not part of `npm run gates`: an upstream disclosure turns it red with no change of ours, and that must not read as a broken SPA.
- `npm audit` must stay at 0 with devDependencies included. Two of them are where real browser-facing bugs came from: `@hey-api/openapi-ts` emits `buildClientParams` INTO `shared/api`, and one such flaw was rated only "moderate", so never gate on `--audit-level` or `--omit=dev`.
- `frontend/package.json` carries two `overrides`, both load-bearing: `js-yaml ^4.3.0` (the quadratic merge-key parse, reached via both openapi-ts and steiger; fixed inside 4.x so no dependent needs a major), and `zod-validation-error ^4` scoped to `eslint-plugin-react-hooks` (7.1.1 imports the `/v4` subpath but declares `^3.5.0 || ^4.0.0`, and steiger's `^3.5.3` wins the dedupe — ESLint dies at load without this).
- `eslint.config.js` names `rules-of-hooks` and `exhaustive-deps` instead of spreading `reactHooks.configs.recommended`: since plugin v6 that preset also enables the fifteen React Compiler rules. The plugin sits on v7 only because it is the first line peering on ESLint 10. Adopting those rules is its own change — they currently flag eight spots in existing code (purity, refs, set-state-in-effect, incompatible-library).

Run frontend gates from `context/setup.md`; `frontend/package.json` and boundary tests are the executable source of truth.
