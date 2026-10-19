---
name: architecture
description: Backend flow, stack, services, gateways and system design.
last_updated: 2026-10-19
edges:
  - target: context/conventions.md
    condition: backend implementation or review conventions
  - target: patterns/INDEX.md
    condition: the change is a repeatable implementation task
  - target: patterns/add-auth.md
    condition: changing authentication
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

The account lifecycle journal has no FK to `accounts` and outlives both the log purge and the account; its writes never raise into the path that produced the event.

The operator bot is one more reader of the in-process log bus: it maps a few warming and spam-status events to Telegram messages, throttled in memory per account and kind, and a failed send is logged by status only, never breaking the pipeline. Its bot token is stored like the LLM keys and leaves the API only as a mask.

Inbound telephony is the one machine-to-machine route outside the session gate: SMS gateways post to the webhook with a shared secret header (off until `TELEPHONY__WEBHOOK_SECRET` is set), and a replay window plus a timestamp skew check stand in for a session. A code is typed into a phone only while the operator opened a wait for it, the SMS is from that wait's platform, the Telegram app shows its code screen, and no start or warming session holds the pod; otherwise the wait ends `failed` with a stable code. Waits, the replay window and the inbox are in-process state, like the other runtimes.
