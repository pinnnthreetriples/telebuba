---
last_updated: 2026-10-01
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
- Vitest logic coverage stays ≥80%; Steiger, ESLint, Prettier, TypeScript, the design-system gates, tests and build must pass. CVE checks are separate CI jobs.
- `useMutation` per-call callbacks are safe only with one structurally exclusive caller. Concurrent/per-row/loop/unmountable flows use `mutateAsync` and promise handlers; the loop case is linted.
- Query re-seed tests must return meaningfully changed data: TanStack structural sharing can keep equal payload identity and make an effect look dead.
- Account chats live inside the account card. Dialogs and history use paged entity queries; opening a conversation marks it read. Inbox SSE only invalidates the relevant queries, while media previews load on demand and downloads use the authenticated same-origin stream.

## The design system is a closed set

Base tokens form a closed set for colour, typography, radius, elevation, motion and rhythm. Component and layout settings select existing token keys; recipes map them to complete static classes for Tailwind. Local values outside this system create a second source of truth, so gates reject them:

- `design-tokens/no-raw-values` (local rule, `frontend/eslint-rules/`) is an ESLint **error**: a raw hex or an arbitrary `[7px]` fails `npm run lint`. It reads string literals anywhere, not only in `className`, because a style constant hoisted to the top of a module is the same decision written somewhere the reviewer will not look. Its carve-outs are deliberate and reasoned in the rule's own header; read that before reaching for a suppression, and prefer an inline one over widening the pattern.
- `npm run ds:css` checks `src/**/*.css` for raw lengths, colours and numeric typography. One-off geometry or motion needs a `design-token-exception:` comment immediately before its declaration with a reason.
- `npm run ds:dead` closes the other end, the one the lint rule cannot see: a rung the config declares that nothing in `src` wears fails the gates. An unworn rung is not spare capacity, it is one more choice to make — so take it out of the config or put it on. A dimension only one component ever needs is the reverse mistake: giving it a rung puts a name with a single wearer in the canon, which is how a closed set reopens.
- `frontend/docs/design-system.html` is GENERATED from tokens. Regenerate with `npm run ds:doc`; `npm run ds:doc:check` fails on drift. Never hand-edit generated values.
- Component contracts reject visual overrides, including imported constants and spreads. Product `className` owns surrounding layout; typed variants preserve distinct geometry and narrow reasoned exceptions.

Dependency versions, overrides, advisories and generated-client quirks are intentionally not duplicated here; `package.json`, lockfile, CI and focused regression tests are their source of truth.

Storybook uses real React components and offline product fixtures. Controls change examples; saving central settings propagates through HMR. Preview servers block backend access. Vite catalog snapshots remain the pixel gate; screenshots also support visual review.

Account-editor empty actions share a visual shell; profile tabs signal hover lightly and selection strongly, while fixed dialog height prevents jumps. Keep page behavior in its owning slice. Shared bars share geometry while progress, capacity, and scores retain distinct meanings.

Route loaders recheck stale first-screen data on return and wait up to 1.5s before swapping pages; the active rail tracks the displayed route. Pages still own errors and live refresh.

Run frontend verification from `context/setup.md`.
