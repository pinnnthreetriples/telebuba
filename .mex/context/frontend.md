---
name: frontend
description: React/FSD rules for frontend/.
triggers: [frontend, react, fsd, tanstack, vite, i18n]
edges:
  - target: context/architecture.md
    condition: API boundary
  - target: context/ci.md
    condition: frontend gates
  - target: patterns/add-frontend-slice.md
    condition: new frontend slice
last_updated: 2026-07-16
---

# Frontend
React 19 + strict TypeScript + Vite. Server access is only through `/api/v1` using the generated `shared/api` client and TanStack Query.

## FSD order
`app → routes → pages → widgets → features → entities → shared`

A layer imports only lower layers. Cross-slice imports use the slice `index.ts`; internals stay private. `routes` are thin, pages compose, widgets group UI, features own interactions, entities own business nouns, and `shared` owns generic API/UI/lib/config/i18n.

## Rules
- Never call backend URLs directly or hand-edit generated API files.
- No `any` or ignored type errors without a specific justification.
- All user-facing text and formatting use react-i18next/`Intl` (`ru`, `en`).
- Reuse Tailwind tokens and local `shared/ui`; avoid duplicated ad-hoc values.
- Runtime frontend config uses `VITE_*`.
- Backend business policy is not duplicated in the SPA.

## Gates
```bash
cd frontend
npm run boundaries
npm run lint
npm run format
npm run typecheck
npm run test
npm run build
```
Vitest coverage floor is 80%. `package.json`, Steiger, TypeScript, ESLint, and tests are the executable source of truth.