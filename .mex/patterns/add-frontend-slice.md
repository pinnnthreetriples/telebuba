---
last_updated: 2026-07-16
---

# Add Frontend Slice
1. Expose missing backend data first and regenerate `shared/api`.
2. Choose the lowest correct FSD layer; export through `index.ts` and import only lower/public APIs.
3. Put server calls in query/API hooks, never components.
4. Add RU/EN i18n keys and reuse Tailwind tokens/local `shared/ui`.
5. Test behavior with Vitest/RTL.
6. Run `cd frontend && npm run gates && npm run build`.

Verify: no direct URLs, hand-edited generated client, literal display strings or boundary violations; strict types and ≥80% logic coverage remain green.
