---
name: services
description: Business-logic layer rules and domain map.
triggers: [service, business logic, orchestration, state transition]
edges:
  - target: context/architecture.md
    condition: layer placement
  - target: context/telegram.md
    condition: Telegram action
  - target: patterns/add-service.md
    condition: new service
last_updated: 2026-07-16
---

# Services
`services/` owns business policy, state transitions, orchestration, and runtime workflows. API routes and runtime tasks call the same service functions.

## Domains
- `accounts/`: account, session, login, profile/media, channels.
- `warming/`: settings, board, pacing, cycles, task runtime.
- `neurocomment/`: campaigns, listener, generation, readiness, challenge handling.
- `auth/`, `proxies.py`, `content.py`, `dialogues.py`, `events.py`, `logs.py`, `spam_status.py`, `trust.py`.

## Rules
- Public I/O functions are async and use Pydantic contracts.
- Services may compose other services.
- DB, Telegram, HTTP providers, logging, and other I/O go through `core/` adapters.
- No FastAPI, UI, SQLAlchemy, Telethon, provider SDK, or raw HTTP imports.
- Small domains use one module; large domains use a package with a thin `__init__.py`.
- Tests mock adapter boundaries and cover public success/failure behavior.