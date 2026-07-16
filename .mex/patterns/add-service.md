---
name: add-service
description: Add or extend business logic in services/.
triggers: [add service, new service, business logic]
edges:
  - target: context/services.md
    condition: service rules
  - target: patterns/add-telegram-task.md
    condition: Telegram action
last_updated: 2026-07-16
---

# Add Service

## Steps
1. Define cross-layer Pydantic contracts in `schemas/`.
2. Add logic to `services/<domain>.py` or a focused package submodule; keep `__init__.py` thin.
3. Delegate DB, Telegram, providers, config, and logging to `core/` gateways.
4. Add service tests that mock gateways and cover success/failure branches.
5. Run relevant pytest, lint, type, and quality gates.

## Verify
No FastAPI, UI, SQLAlchemy, Telethon, or raw provider HTTP imports; public I/O is async and typed; HTTP concerns remain in `api/`; package roots do not accumulate unrelated logic.