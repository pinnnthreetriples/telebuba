---
last_updated: 2026-07-26
---

# Add Frontend Slice
1. Expose missing backend data first and regenerate `shared/api`.
2. Choose the lowest correct FSD layer; export through `index.ts` and import only lower/public APIs.
3. Server calls belong in an entity's `api/` segment: wrap the generated query/mutation options in `entities/<noun>/api/*.queries.ts` (aliasing the `…Options`/`…QueryKey` names) and re-export them from the entity barrel. Nothing outside `entities/` and `shared/` imports `shared/api/@tanstack/react-query.gen` or calls the SDK — pages, widgets and features consume the barrel. `import type` straight from `@/shared/api` is the exception and is used everywhere.
4. Add RU/EN i18n keys and reuse Tailwind tokens/local `shared/ui`.
5. Test behavior with Vitest/RTL.
6. Run `cd frontend && npm run gates && npm run build` (`gates` = steiger → eslint → prettier → tsc → vitest).

Verify: no direct URLs, hand-edited generated client, literal display strings or boundary violations; strict types and ≥80% logic coverage remain green. `eslint` runs `--max-warnings 0`, so the rules configured as `warn` (`exhaustive-deps`, `only-export-components`) fail the gate exactly like errors — hence non-component exports live in a `.ts` sibling, not the `.tsx`.
