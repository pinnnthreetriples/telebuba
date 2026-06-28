---
name: architecture
description: How the major pieces of this project connect and flow, plus the exact folder layout and the canonical layer import matrix.
triggers:
  - "architecture"
  - "system design"
  - "how does X connect to Y"
  - "integration"
  - "flow"
  - "folder structure"
  - "layers"
  - "import rules"
edges:
  - target: context/stack.md
    condition: when specific technology details are needed
  - target: context/decisions.md
    condition: when understanding why the architecture is structured this way
  - target: context/conventions.md
    condition: when the question is about coding rules rather than structural rules
  - target: context/frontend.md
    condition: when working in the React SPA (frontend/)
  - target: context/telegram.md
    condition: when working with Telethon clients or Telegram interaction
  - target: context/warming.md
    condition: when working with runtime workflow tasks
  - target: context/logging.md
    condition: when working with the three-tier logging architecture
last_updated: 2026-06-28
---

# Architecture

## System Overview

The app is a **split stack**: a standalone **React + TypeScript SPA** (`frontend/`) over a
thin **FastAPI + uvicorn JSON API** (`api/`) exposed at `/api/v1` on top of the existing
`services/`.

A user action in the SPA → a TanStack Query hook calls the generated client → an `/api/v1`
route in `api/` validates the request (Pydantic), delegates to a `services/` domain
package/module, and serializes the result back as a Pydantic model → the service uses `core/`
gateways for I/O: SQLite through `core/db.py` + `core/repositories/`, Telegram through
`core/telegram_client/`, Gemini through `core/gemini.py`, logging through `core/logging.py`.

The API is **locale-neutral** — it returns status codes/enums and ISO-8601 timestamps, never
pre-translated display text; the SPA owns all presentation and i18n (see `context/frontend.md`).

Long-running runtime workflows are owned by service-layer async tasks, started/stopped from
the FastAPI **`lifespan`** (replacing NiceGUI's `on_startup`/`on_shutdown`). The warming
runtime uses per-account `asyncio.Task`s owned by `services/warming/_runtime.py`; neurocomment
uses an event listener + periodic sweep. APScheduler is not used.

**uvicorn runs single-worker** — the runtimes are in-process asyncio tasks; multiple workers
would duplicate Telegram work and race the SQLite DB.

## Folder Structure

Python stays at the repo root; the SPA lives in a sibling `frontend/` tree.

```text
telebuba/
├── main.py                       FastAPI/uvicorn composition root: lifespan (runtime start/stop) + routers + static mount
├── pyproject.toml                uv project + strict test/lint/security gates
├── .env                          local secrets (gitignored)
├── .env.example                  committed template; architecture tests keep it synced with core/config.py
├── api/                          UI-thin top layer (replaces features/): /api/v1 routes — validate → call service → serialize
│   ├── __init__.py               FastAPI app factory / router assembly
│   ├── v1/                       versioned routers, one module per domain (accounts, warming, neurocomment, logs, settings, auth)
│   ├── deps.py                   shared dependencies (e.g. Depends(get_current_user))
│   └── errors.py                 error-envelope mapping (incl. 422 → {error:{code,message,fields?}})
├── core/                         shared infrastructure — only layer touching third-party SDKs
│   ├── config.py                 pydantic-settings; nested namespaces (incl. settings.api, settings.auth)
│   ├── db.py                     SQLite metadata, table definitions, engine lifecycle, generic helpers, compatibility re-exports
│   ├── migrations.py             versioned, append-only migration registry — apply_migrations() runs on engine init
│   ├── repositories/             per-aggregate DB query modules (incl. users for auth)
│   ├── telegram_client/          Telethon gateway package; public API re-exported from core.telegram_client
│   ├── gemini.py                 HTTP gateway for Gemini
│   ├── auth.py                   password hashing + JWT encode/decode (the only place tokens are minted/verified)
│   └── logging.py                loguru + SQLite logs + optional Sentry gateway
├── schemas/                      Pydantic models; shared types, no behavior, no I/O
│   ├── <domain>.py               one file per domain contract
│   └── api.py                    cross-cutting wire types: error envelope, generic Page[T] = {items, next_cursor}
├── services/                     business logic — pure, reusable, no UI, no SDK imports
│   ├── accounts/                 account/session/profile/proxy operations
│   ├── warming/                  runtime workflow domain package
│   ├── neurocomment/             event-driven campaign-comment package
│   └── auth/                     auth policy (verify credentials, issue/rotate session) over core/auth.py + users repo
├── frontend/                     React + TS (strict) + Vite SPA — Feature-Sliced Design (see context/frontend.md)
│   ├── src/app|routes|pages|widgets|features|entities|shared
│   ├── tailwind.config.*         design tokens (single source of truth)
│   └── package.json              FE deps + gate scripts (lint/boundaries/tsc/vitest/playwright/gen-api)
└── tests/                        mirrors source tree; pytest + property tests + architecture tests
```

A small domain may start as a single module (e.g. `api/v1/logs.py`); once it grows it becomes
a package with a thin `__init__.py` and cohesive submodules.

## Layer Model

The backend has four layers, top (UI-thin) to bottom (infra). The **frontend is a separate
tree** governed by its own law (`context/frontend.md`), reaching the backend only over HTTP.

```text
frontend/  (React SPA)  ── HTTP /api/v1 ──┐
                                          ▼
main.py
  └─ api/          UI-thin /api/v1 routes. Validate → call service → serialize. NO business logic.
       └─ services/   business logic, state transitions, runtime workflow orchestration.
            └─ core/  infrastructure gateways: db/repositories, telegram_client, gemini, auth, config, logging.
                 └─ schemas/    Pydantic models — pure data, no project imports.
```

`schemas/` is shared types, not a downstream layer. All layers may import schema types;
schemas must not import layers.

## Allowed Imports

| Layer | May import |
| --- | --- |
| `api/` | `services/`, `schemas/`, `core.config`, `core.logging`, `fastapi`. **Only** those — UI-thin. |
| `services/` | other `services/`, `core/`, `schemas/`. Composition between services is allowed. |
| `core/` | `schemas/`, stdlib, third-party SDKs. Not `api/` or `services/`. |
| `schemas/` | `pydantic`, `typing`, stdlib typing helpers only. |
| `frontend/` | npm deps + its own FSD layers (import matrix in `context/frontend.md`). Reaches the backend only via `/api/v1`. |
| `tests/` | anything (verification layer). |

The `api/` allowlist is the descendant of the old `features → {config, logging}` firewall —
the same gateway discipline `features/` had (data only via services), re-pointed to `api/`.

## Forbidden Imports (each breaks a non-negotiable)

| In… | Must NOT import | Why |
| --- | --- | --- |
| any `api/` | `core.db`, `core.repositories`, `core.telegram_client`, `core.gemini`, `sqlalchemy`, `telethon` | Rule 1/6 — `api/` reaches data only through `services/`; the executable firewall |
| any `services/` | `sqlalchemy`, `telethon`, raw provider SDK/HTTP clients | Rule 6 — services use `core/` adapters, never SDKs directly |
| any `services/` | `fastapi`, anything UI/transport-specific | Rule 11 — services are transport-agnostic (reusable from API, scripts, tasks, tests) |
| any module outside `core/logging.py` | `loguru`, `sentry_sdk` | Rule 4 — logging only via `core/logging.py` |
| any module outside `core/config.py` | `os.environ`, raw `dotenv` | Rule 3 — config only via `core/config.py` |
| any module outside `core/auth.py` | the JWT library directly | tokens are minted/verified in one place |
| `schemas/` | `core/`, `services/`, `api/`, any I/O library | Rule 5 — schemas are pure data, no behavior |
| `core/` | `services/`, `api/` | Rule 5 — core does not know about services or the API |

Rule numbers reference the canonical list in `context/conventions.md`. `.mex/AGENTS.md`
mirrors the same rules in one-line form. `tests/test_architecture.py` enforces the `api/`
allowlist (re-pointed from `features/` in slice #164).

## Cross-cutting API contract

- **Error envelope** — every error response is `{error: {code, message, fields?}}`. FastAPI's
  default 422 validation error is remapped into the same shape (mapping in `api/errors.py`).
- **Pagination** — generic cursor pagination via `Page[T] = {items, next_cursor}` in
  `schemas/api.py`.
- **Locale-neutral** — codes/enums + ISO-8601 timestamps only; no display text on the wire.
- **Auth** — `Depends(get_current_user)` guards protected routes; the session is an
  **HttpOnly + Secure + SameSite cookie with a sliding TTL** (no refresh rotation). `role`
  column exists from day one; no RBAC until a second role appears. No public signup
  (admin-seeded users).

## Data Crossing Layers

Any public function whose caller is in a different layer takes and returns Pydantic models
from `schemas/` or `None`. Raw `dict`, `tuple`, and `list` of raw values are forbidden as
cross-layer parameters or return values. If multiple items must cross a boundary, wrap them in
a Pydantic response model (e.g. `Page[T]`). Inside a single function or private module,
regular Python data structures are fine.

**Sanity check:** a repository maps SQLAlchemy rows into a `schemas/` model before returning;
a route returns a `schemas/` model (FastAPI serializes it); the SPA receives JSON typed by the
generated client.

## Key Components

- **`main.py`** — composition root: build the FastAPI app, mount `/api/v1` routers, mount
  `frontend/dist` via `StaticFiles` + a catch-all returning `index.html`, and run the runtimes
  via `lifespan`. `uvicorn.run(app)` lives here (single-worker).
- **`api/`** — UI-thin routers. Validate the request, call a service, serialize the result.
  No business logic; no direct DB/Telegram access.
- **`core/config.py`** — typed settings via `pydantic-settings`, nested namespaces per domain
  (`settings.warming`, `settings.gemini`, `settings.telegram`, `settings.api`, `settings.auth`, …).
- **`core/auth.py`** — password hashing + JWT encode/decode; the only place tokens are minted
  or verified.
- **`core/db.py` / `core/repositories/`** — SQLite plumbing + per-aggregate query modules
  (accounts, warming, logs, content, device_fingerprint, dialogues, spam_status, neurocomment, users).
- **`core/telegram_client/` / `core/gemini.py` / `core/logging.py`** — the other gateways.
- **`schemas/`** — Pydantic models flowing between API, services, core, and DB.
  `schemas/api.py` owns the error envelope + `Page[T]`; `schemas/telegram_actions.py` declares
  Telegram actions as typed Pydantic classes.
- **`services/`** — account lifecycle, warming runtime, neurocomment, auth policy; transport-agnostic.
- **`frontend/`** — the React SPA (FSD); the only UI.

## External Dependencies

- **Telegram (via Telethon)** — all calls funnel through `core/telegram_client/` and typed actions.
- **SQLite (via SQLAlchemy)** — local file DB for the current single-process deployment. DB
  access goes through `core/db.py` and `core/repositories/`.
- **Gemini API (via httpx)** — AI text generation through `core/gemini.py` only.
- **FastAPI + uvicorn** — the JSON API and HTTP server (single-worker).
- **React + Vite SPA** — the frontend, served as static files in prod and proxied in dev.

## What Does NOT Exist Here

- No NiceGUI — removed in the split-stack pivot (2026-06-28). The UI is the React SPA.
- No multi-worker uvicorn — the in-process runtimes assume one process. Extract runtimes to a
  separate process first if throughput ever demands more workers.
- No remote database, queue, broker, or worker process — SQLite + in-process async runtime.
- No APScheduler — warming uses asyncio tasks; neurocomment an event listener + periodic sweep.
- No `telegram_outbox` — direct executor + persisted per-cycle state is the current crash-safety model.
- No business logic in `api/` — routes delegate to services.
- No direct DB/Telegram access from `api/` — only through `services/`.
- No raw `dict`/`tuple`/`list` crossing layer boundaries.
- No third-party SDKs (Telethon, SQLAlchemy, loguru, sentry_sdk, raw provider HTTP clients)
  outside their dedicated `core/` wrapper.
- No pre-translated display text on the API — the SPA owns i18n.
</content>
