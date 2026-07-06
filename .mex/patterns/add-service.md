---
name: add-service
description: Add or extend a service module/package — pure business logic, async, Pydantic at edges, no SDK imports. Pair with tests under tests/services/.
triggers:
  - "add service"
  - "new service"
  - "services/"
  - "business logic"
edges:
  - target: context/services.md
    condition: always — defines what belongs in this layer and what doesn't
  - target: context/conventions.md
    condition: when in doubt about a rule (UI-thin api/, gateways, async + types)
  - target: patterns/add-telegram-task.md
    condition: when the service drives Telegram via typed actions
  - target: patterns/add-api-endpoint.md
    condition: when an /api/v1 endpoint is going to call this service
last_updated: 2026-07-06
---

# Add a Service

## Context

Read `context/services.md` for the layer definition. Hard rules:
- Small domain: `services/<domain>.py`. Large domain: `services/<domain>/` package.
- Pure business logic. No `fastapi`, no `sqlalchemy`, no `telethon`, no raw provider HTTP imports.
- All I/O goes through `core/` adapters.
- Public cross-layer functions take and return Pydantic models from `schemas/` or `None`.

## Steps

1. **Schema first.** Add request/result models in `schemas/<domain>.py`. If new Telegram actions are needed, extend `schemas/telegram_actions.py`.
2. **Create or extend the service domain.** Use `services/<domain>.py` for a small domain or `services/<domain>/` for a domain that already has multiple concerns.
3. **Keep the package root thin.** In a package, `__init__.py` should re-export public API or contain minimal compatibility/orchestration. Move real slices into submodules.
4. **Use the right gateways:**
   - DB: `core/db.py` compatibility re-exports or `core/repositories/`.
   - Telegram: `from core.telegram_client import execute` + typed action from `schemas/telegram_actions.py`.
   - HTTP providers: `core/<provider>.py` wrapper.
   - Logging: `from core.logging import log_event`.
   - Config: `from core.config import settings` then `settings.<namespace>.<field>`.
5. **Test — prefer `/tdd`.** Add/update tests under `tests/services/`:
   - Mock `core/` adapters.
   - Cover happy path + failure paths.
   - Keep branch coverage ≥ 90%.
6. **Run gates.** `uv run pytest` and relevant lint/type/security gates.

## Gotchas

- Do not put HTTP concerns (status codes, response envelopes) inside a service. The service returns a typed result; `api/` serializes it.
- Do not import from `api/`. Pull shared behavior into services/core/schemas.
- Sync I/O blocks the single-worker event loop. Use `await`; for unavoidable sync work, `asyncio.to_thread`.
- A service package root that grows past simple API re-export/orchestration is a smell — split by subdomain.
- Do not add raw list/dict returns for convenience; create a response schema.

## Verify

- [ ] Service domain is in `services/<domain>.py` or `services/<domain>/`
- [ ] No `fastapi` / `sqlalchemy` / `telethon` / raw `httpx` imports in the service
- [ ] Public cross-layer functions are async when they perform I/O and take/return Pydantic models
- [ ] Telegram actions go through `core.telegram_client.execute(account_id, action)` with a typed action schema
- [ ] Config used as `settings.<namespace>.<field>`
- [ ] Tests under `tests/services/` mock `core/`; cover happy + failure paths
- [ ] `uv run pytest` passes

## Debug

- Service can't be imported from a test → likely circular import via `core/` or an accidental feature import.
- Test passes but coverage drops below 90% → unhandled branch in the service; add a failure-path test.
- SDK exception leaks past a service → the core gateway should classify it and return a typed result; check the gateway boundary.

## After

Run the GROW step from `ROUTER.md` (update `state/active.md`, touch any out-of-date `context/`, bump `last_updated`).
