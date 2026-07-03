---
name: add-frontend-slice
description: Add a screen, widget, feature, or entity to the React SPA following Feature-Sliced Design.
triggers:
  - "add screen"
  - "new page"
  - "frontend slice"
  - "react component"
  - "fsd"
edges:
  - target: context/frontend.md
    condition: always — the frontend has its own FSD law and gate set
  - target: patterns/add-api-endpoint.md
    condition: when the slice needs data the backend does not expose yet
last_updated: 2026-06-28
---

# Add a Frontend Slice

## Context

Read `context/frontend.md` first. Key constraints:
- FSD layers `app/routes/pages/widgets/features/entities/shared`; a layer imports only lower
  layers; no sideways imports within a layer.
- Every slice exposes a public API via `index.ts`; cross-slice imports go through it only.
- All server I/O goes through `shared/api` (the generated hey-api client + TanStack Query) or an
  entity `api/` segment — components never `fetch` directly.
- TypeScript strict. All UI strings via i18n. Colors/spacing via Tailwind tokens.

## Steps

1. **Backend first if needed.** If the data isn't on `/api/v1` yet, do `add-api-endpoint.md`
   and regenerate the client so `shared/api` has the typed call.
2. **Entity.** Model the business noun in `entities/<noun>/`: `api/` (TanStack Query hooks over
   the generated client), `model/` (derived state), `ui/` (card/row). Export via `index.ts`.
3. **Feature.** Put a single user interaction (assign-account, start-warming) in
   `features/<verb-noun>/` with its own state. Export via `index.ts`.
4. **Widget (if reused).** Compose a self-contained block in `widgets/<name>/` when more than
   one page needs it.
5. **Page.** Assemble the screen in `pages/<name>/` from widgets/features/entities. Keep it thin.
6. **Route.** Register the route in `routes/` (TanStack Router); wire params; guard with the
   protected layout if it needs auth.
7. **i18n.** Add every string to `shared/i18n/ru.json` (+ `en.json` key) — no literal text in JSX.
8. **Tokens.** Use Tailwind classes from `tailwind.config`; don't hardcode hex/px that
   duplicates a token. Use `shared/ui` (shadcn/Radix) primitives.
9. **Test.** Vitest + RTL for logic (hooks/features). Keep ≥ 80% on logic
   (presentational/generated/shadcn excluded).
10. **Run gates** (from `frontend/`): `npm run gates` (eslint/prettier/boundaries/tsc/vitest).

## Gotchas

- Reaching into another slice's internals (`entities/account/model/...`) → import its `index.ts`
  instead; the boundary linter (Steiger) will fail the build otherwise.
- A component calling `fetch`/`axios` → move the call into `shared/api` or an entity `api/` hook.
- Hand-editing the generated client → never; regenerate via the gen-api script.
- Literal RU/EN text in JSX → move to the i18n resource.

## Verify

- [ ] Slice lives in the right FSD layer; only lower-layer imports; no sideways imports
- [ ] Slice exposes a public `index.ts`; cross-slice imports go through it
- [ ] Data access only via `shared/api` / entity `api/` (TanStack Query over the generated client)
- [ ] No literal user-facing strings — all via i18n; colors/spacing via tokens
- [ ] `npm run gates` passes (boundary-lint + tsc-strict + eslint + vitest ≥ 80%)

## Reference implementation

The **Accounts screen (#167)** is the canonical example of this pattern end-to-end:
`api/v1/accounts.py` (thin routes incl. multipart uploads) → generated client →
`entities/account` (status model + `StatusBadge` + query/mutation re-exports) →
`widgets/accounts-table` (TanStack Table: sorting/selection) →
`pages/accounts` (toolbar + cursor pagination + wired mutations) → i18n labels for
every string. Copy its shape for the remaining screens (#169–#172).

## After

Run the GROW step from `ROUTER.md` (update `state/active.md`, bump `last_updated`).
</content>
