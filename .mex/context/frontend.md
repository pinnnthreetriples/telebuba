---
name: frontend
description: Frontend law — the React SPA's Feature-Sliced Design layers, one-directional import matrix, slice public-API rule, data-access discipline, i18n/tokens rules, and the FE gate set. Load when writing or reviewing any code under frontend/.
triggers:
  - "frontend"
  - "react"
  - "fsd"
  - "feature-sliced"
  - "tanstack"
  - "tailwind"
  - "shadcn"
  - "vite"
  - "i18n"
  - "boundary"
  - "slice"
edges:
  - target: context/architecture.md
    condition: when the question is how the SPA talks to the backend
  - target: context/decisions.md
    condition: when understanding why the frontend is structured this way (the 3 ADRs of 2026-06-28)
  - target: context/ci.md
    condition: when wiring or debugging the frontend CI jobs
  - target: patterns/add-frontend-slice.md
    condition: when adding a screen, widget, or entity to the SPA
last_updated: 2026-06-28
---

# Frontend

The UI is a standalone **React + TypeScript (strict) + Vite** SPA living in a sibling
`frontend/` tree. It talks to the Python backend **only** over the `/api/v1` JSON API
(see `context/architecture.md`). The Python non-negotiables (`conventions.md`) govern
the backend; **this file is the law for everything under `frontend/`.**

The three ADRs of 2026-06-28 in `context/decisions.md` are the source: *React-SPA-over-FastAPI*,
*frontend-owns-i18n*, *Feature-Sliced Design enforced in CI*.

## Methodology — Feature-Sliced Design (FSD)

Full FSD layers, highest to lowest. A layer may import **only from lower layers**; **no
sideways imports within a layer**. This mirrors the backend's layer isolation — one mental
model for the whole repo.

```text
app/        providers, router root, global styles, error boundary, query client.
routes/     route definitions (TanStack Router) — thin: pick a page, wire params.
pages/      one screen each (Accounts, Warming, Neurocomment, Logs, Settings, Login).
widgets/    composite, self-contained UI blocks reused across pages (nav bar, board column).
features/   a single user interaction with its own state (assign-account, start-warming).
entities/   business nouns + their api/query hooks + cards (account, campaign, log-row).
shared/     framework-agnostic building blocks: shared/api (the generated client + query
            setup), shared/ui (shadcn/Radix primitives), shared/lib, shared/config, shared/i18n.
```

A layer is a directory of **slices** (e.g. `entities/account/`, `features/assign-account/`).
Each slice is internally organized by **segments**: `ui/`, `model/` (state/hooks),
`api/`, `lib/`, `config/`.

### Import matrix

| Layer | May import from |
| --- | --- |
| `app` | routes, pages, widgets, features, entities, shared |
| `routes` | pages, widgets, features, entities, shared |
| `pages` | widgets, features, entities, shared |
| `widgets` | features, entities, shared |
| `features` | entities, shared |
| `entities` | shared |
| `shared` | nothing above it — leaf layer (may use npm deps + other shared slices' public API) |

No layer imports a sibling slice's internals; no upward imports ever.

### Slice public API (`index.ts`)

- **Every slice exposes its public surface through `index.ts`.** Everything else in the slice
  is private.
- Cross-slice imports go through the slice's `index.ts` only — never reach into
  `entities/account/model/use-account.ts` from outside `entities/account`.
- This is the load-bearing rule the boundary linter enforces; without it, layered conventions
  erode (the backend learned this — see the *executable firewall* ADR).

## Hard rules

### 1. Data access only through `shared/api`
Components never call `fetch`/`axios` and never hit a URL directly. **All** server I/O goes
through the generated **`@hey-api/openapi-ts`** client wrapped in **TanStack Query** hooks,
which live in `shared/api` (cross-cutting) or an entity's `api/` segment. The generated
client is regenerated from the backend OpenAPI and **drift-checked in CI** (see `ci.md`);
never hand-edit it.

### 2. TypeScript strict
`tsc --strict` (with `noUncheckedIndexedAccess`) is a gate. No `any` to dodge a type; no
`@ts-ignore` without a one-line justification naming the upstream cause.

### 3. All UI strings via i18n
Every user-facing string and every relative-time / number / date format goes through
**react-i18next** (`ru` default, `en` next) + `Intl`. **No literal display text in JSX.**
The API is locale-neutral — it returns status **codes/enums** and **ISO-8601 timestamps**,
never pre-translated text; the FE owns all presentation (this reverses the old service-side
RU-label approach — those maps moved into `shared/i18n/ru.json` / `en.json`). Log rows
localize a stable event **code + structured params** (staged refinement, not day-one).

### 4. Design tokens live in `tailwind.config`
Colors, spacing, fonts, radii, shadows — the single source of truth, **extracted from the
design file before `web/` is deleted** (issue #173). Components consume tokens via Tailwind
classes; no ad-hoc hex/px that duplicates a token. shadcn/ui (Radix) primitives live in
`shared/ui`.

### 5. Frontend config via `VITE_*`
Build/runtime config comes from Vite env vars (`import.meta.env.VITE_*`), not hardcoded.
This is the FE counterpart to the backend's "config in `core/config.py`" rule — it does
**not** apply to the frontend (no Python settings on this side).

## Stack

| Concern | Choice |
| --- | --- |
| Build / dev server | Vite (dev: vite + `/api` proxy to uvicorn) |
| Routing | TanStack Router |
| Server state | TanStack Query (over the hey-api client) |
| Tables | TanStack Table |
| Forms | TanStack Form + Zod |
| Styling | Tailwind + shadcn/ui (Radix) |
| Generated client | `@hey-api/openapi-ts` from `/api/v1` OpenAPI |
| Error reporting | Sentry React |
| Unit / component tests | Vitest + React Testing Library |
| E2E smoke | Playwright (critical flows) |
| Assets | self-hosted Inter + flag-icons — **zero CDN** |

## Gate set (CI + local)

The **boundary linter is the load-bearing gate** — layered conventions without an executable
check erode. All of these block a merge:

- **Boundary lint** — **Steiger** (or `eslint-plugin-boundaries`): enforces the FSD import
  matrix + slice public-API rule.
- **ESLint** + **Prettier** — lint + format.
- **`tsc --strict`** — type check, no emit.
- **Vitest** — unit/component tests, **coverage floor 80%** (generated client, shadcn
  primitives, and pure-presentational components excluded; logic — hooks/features/api — tested
  strictly).
- **Playwright smoke** — critical flows (login, each screen loads, one happy-path action) as
  the integration net.
- **gen-api drift** — the committed generated client must match a fresh generation from the
  backend OpenAPI (shared with `ci.md`).

80% UI coverage + E2E is the honest floor: 90% branch on presentational components is
high-cost / low-signal; logic is tested strictly and flows are covered by Playwright.

## What does NOT belong here

- No data fetching outside `shared/api` / an entity `api/` segment.
- No hand-edits to the generated client.
- No literal user-facing strings — everything via i18n.
- No business logic the backend already owns — the SPA validates, calls `/api/v1`, renders.
- No CDN dependencies — assets are self-hosted (offline-robust serving).
</content>
</invoke>
