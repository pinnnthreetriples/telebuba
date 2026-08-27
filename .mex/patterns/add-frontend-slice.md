---
last_updated: 2026-08-27
edges:
  - target: context/frontend.md
    condition: React, FSD layering, TypeScript, i18n or frontend gates
  - target: context/conventions.md
    condition: shared repository conventions
---

# Add Frontend Slice
1. Expose missing backend data first and regenerate `shared/api`.
2. Choose the lowest correct FSD layer; export through `index.ts` and import only lower/public APIs.
3. Server calls belong in an entity's `api/` segment; wrap generated query/mutation options there and expose them through the entity barrel. UI layers consume that public API rather than generated internals.
4. Add RU/EN i18n keys; compose `shared/ui` primitives instead of re-drawing them, and take every design value from `tailwind.config.ts`. A raw hex or an arbitrary `[7px]` is a lint ERROR, and a rung added to the config that nothing wears fails `ds:dead` — reasoning in `context/frontend.md`.
5. Test behavior with Vitest/RTL.
6. Run `cd frontend && npm run gates && npm run build` (`gates` = steiger → eslint → prettier → tsc → ds:doc:check → ds:dead → vitest). If a token or a primitive's variant set changed, regenerate the canon with `npm run ds:doc` in the same commit rather than editing the page.

Verify: no direct URLs, hand-edited generated client or canon, literal display strings, raw design values or FSD boundary violations; strict types and frontend gates remain green.
