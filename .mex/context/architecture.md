---
last_updated: 2026-09-26
edges:
  - target: context/conventions.md
    condition: backend implementation or review conventions
  - target: patterns/INDEX.md
    condition: the change is a repeatable implementation task
---

# Architecture

`React SPA → /api/v1 → api/ → services/ → core/ → SQLite / Telegram / providers`, with `schemas/` as shared Pydantic contracts.

- `main.py` is the FastAPI composition root and lifespan owner.
- `api/` handles HTTP validation/auth/error mapping/serialization only.
- `services/` owns policy, orchestration and domain state transitions.
- `core/` owns repositories/migrations and external adapters: Telegram, AI, logging/Sentry, SSE and proxy checks.
- `schemas/` is pure contracts; no project-layer imports or I/O.
- `frontend/` is React 19 + strict TypeScript/Vite/FSD and reaches Python only through `/api/v1`.

## Import law
| Layer | May import |
|---|---|
| `api/` | `services`, `schemas`, FastAPI, narrow `core.config` / `core.logging` |
| `services/` | `services`, `core`, `schemas` |
| `core/` | `schemas`, stdlib, third-party packages |
| `schemas/` | Pydantic, typing, stdlib |

Single-process operation is deliberate while SQLite and in-process runtimes own coordination. `tests/test_architecture.py`, manifests and code are the executable source of truth.

Account chats use the same boundary: HTTP contracts and upload admission in `api/`, read/send policy in `services/`, and pooled Telegram I/O plus inbound subscriptions in `core/`. The in-process inbox runtime follows all authorized accounts while the server runs; an authenticated SSE stream carries small invalidation events to the SPA. Chat history and media are read from Telegram on demand rather than mirrored to SQLite.

Scheduled profile photos and stories exist because Telegram cannot schedule them itself: media waits in a content-addressed store under `runtime/`, and one in-process worker publishes one post at a time, paced fleet-wide. `dispatching` is written before the call, so a lost answer or a restart mid-call settles `ambiguous` and is never retried — repeating could post twice. The worker calls the gateway under its own domain, so its floods never write the sticky account status.
