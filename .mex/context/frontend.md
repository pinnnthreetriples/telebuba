---
last_updated: 2026-08-27
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
- Display strings/formatting use react-i18next/`Intl` for `ru` and `en`; compose `shared/ui` primitives rather than re-drawing them, and take every design value from the closed set below.
- Reuse `entities/account` identity helpers/avatar. Account-bearing payloads carry the fields the surface needs instead of reconstructing backend policy in the SPA.
- Frontend configuration uses `VITE_*`.
- Vitest logic coverage stays ≥80%; Steiger, ESLint, Prettier, TypeScript, the two design-system gates, tests and build must pass. CVE checks are separate CI jobs.
- `useMutation` per-call callbacks are safe only with one structurally exclusive caller. Concurrent/per-row/loop/unmountable flows use `mutateAsync` and promise handlers; the loop case is linted.
- Query re-seed tests must return meaningfully changed data: TanStack structural sharing can keep equal payload identity and make an effect look dead.

## The design system is a closed set

`frontend/tailwind.config.ts` holds every design value the UI is allowed to paint with — colour, type rung, radius, elevation, motion rung, line-height, letter-spacing and unit of rhythm. Write a value past that scale and it is not a shortcut but a second source of truth, which is how one field's styling ended up copied across files under different names and drifting. A closed set only stays closed if reopening it is an error rather than a habit, so three gates hold it:

- `design-tokens/no-raw-values` (local rule, `frontend/eslint-rules/`) is an ESLint **error**: a raw hex or an arbitrary `[7px]` fails `npm run lint`. It reads string literals anywhere, not only in `className`, because a style constant hoisted to the top of a module is the same decision written somewhere the reviewer will not look. It flagged zero sites on the tree it landed on — that is the bar. Its carve-outs are deliberate and reasoned in the rule's own header; read that before reaching for a suppression, and prefer an inline one over widening the pattern.
- `npm run ds:dead` closes the other end, the one the lint rule cannot see: a rung the config declares that nothing in `src` wears fails the gates. An unworn rung is not spare capacity, it is one more choice to make — so take it out of the config or put it on. A dimension only one component ever needs is the reverse mistake: giving it a rung puts a name with a single wearer in the canon, which is how a closed set reopens.
- `frontend/docs/design-system.html` is GENERATED from the config and from the primitives' own variant/size/tone sets. Regenerate with `npm run ds:doc`; `npm run ds:doc:check` fails on drift. Never hand-edit it — the hand-written half is precisely the half that went stale while the gate could not see it.

Dependency versions, overrides, advisories and generated-client quirks are intentionally not duplicated here; `package.json`, lockfile, CI and focused regression tests are their source of truth.

Run frontend verification from `context/setup.md`.
