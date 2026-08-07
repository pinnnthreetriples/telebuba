---
last_updated: 2026-08-06
edges:
  - target: context/conventions.md
    condition: shared repository conventions
  - target: patterns/add-frontend-slice.md
    condition: adding or changing a React/FSD slice
---

# Frontend Rules

React 19 + strict TypeScript + Vite. Server I/O uses the generated `shared/api` client with TanStack Query; never call backend URLs directly or hand-edit generated files.

FSD order is `app → routes → pages → widgets → features → entities → shared`. Import only lower layers and cross slice boundaries through public `index.ts` exports.

- Routes/pages compose; features own interactions; entities own business nouns; `shared` owns generic API/UI/lib/config/i18n.
- Generated TanStack query/mutation options are wrapped in entity `api/` modules; pages/widgets/features consume entity barrels. `import type` from `@/shared/api` is allowed.
- No `any` or ignored type failures without a precise upstream justification.
- Display strings/formatting use react-i18next/`Intl` for `ru` and `en`; reuse Tailwind tokens and `shared/ui`.
- Reuse `entities/account` identity helpers/avatar. Account-bearing payloads carry the fields the surface needs instead of reconstructing backend policy in the SPA.
- Frontend configuration uses `VITE_*`.
- Vitest logic coverage stays ≥80%; Steiger, ESLint, Prettier, TypeScript, tests and build must pass. CVE checks are separate CI jobs.
- `useMutation` per-call callbacks are safe only with one structurally exclusive caller. Concurrent/per-row/loop/unmountable flows use `mutateAsync` and promise handlers; the loop case is linted.
- Query re-seed tests must return meaningfully changed data: TanStack structural sharing can keep equal payload identity and make an effect look dead.

Dependency versions, overrides, advisories and generated-client quirks are intentionally not duplicated here; `package.json`, lockfile, CI and focused regression tests are their source of truth.

Run frontend verification from `context/setup.md`.
