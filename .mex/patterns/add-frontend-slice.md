---
last_updated: 2026-08-04
---

# Add Frontend Slice
1. Expose missing backend data first and regenerate `shared/api`.
2. Choose the lowest correct FSD layer; export through `index.ts` and import only lower/public APIs.
3. Server calls belong in an entity's `api/` segment; wrap generated query/mutation options there and expose them through the entity barrel. UI layers consume that public API rather than generated internals.
4. Add RU/EN i18n keys and reuse Tailwind tokens/local `shared/ui`.
5. Test behavior with Vitest/RTL.
6. Run `cd frontend && npm run gates && npm run build` (`gates` = steiger → eslint → prettier → tsc → vitest).

Verify: no direct URLs, hand-edited generated client, literal display strings or FSD boundary violations; strict types and frontend gates remain green.
