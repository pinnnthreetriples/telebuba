---
name: add-frontend-slice
description: Add a React/FSD slice.
triggers: [add screen, new page, frontend slice, react component]
edges:
  - target: context/frontend.md
    condition: frontend rules
  - target: patterns/add-api-endpoint.md
    condition: missing backend contract
last_updated: 2026-07-16
---

# Add Frontend Slice

## Steps
1. Expose missing backend data first and regenerate `shared/api`.
2. Choose the lowest correct FSD layer: entity, feature, widget, page, then thin route.
3. Export the slice through `index.ts`; import only lower layers and public APIs.
4. Put server calls in API/query hooks, never components.
5. Add RU/EN i18n keys and reuse Tailwind tokens/local `shared/ui`.
6. Test behavior with Vitest/RTL.
7. Run `cd frontend && npm run gates && npm run build`.

## Verify
No direct URLs or hand-edited generated client; no literal user-facing text; strict types; FSD boundaries pass; logic coverage remains at least 80%.