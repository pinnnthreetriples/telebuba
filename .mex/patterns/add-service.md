---
name: add-service
description: Add a new service module — pure business logic, async, Pydantic at edges, no SDK imports. Always paired with a test file under tests/services/.
triggers:
  - "add service"
  - "new service"
  - "services/"
  - "business logic"
edges:
  - target: context/services.md
    condition: always — defines what belongs in this layer and what doesn't
  - target: context/conventions.md
    condition: when in doubt about a rule (UI-thin features, gateways, async + types)
  - target: patterns/add-telegram-task.md
    condition: when the service drives Telegram via typed actions
  - target: patterns/add-feature.md
    condition: when a feature is going to call this service
last_updated: 2026-06-10
---

# Add a Service

## Context

Read `context/services.md` for the layer definition. Hard rules:
- `services/<domain>.py` — one new file per domain (`warming`, `accounts`, `comments`, `telegram_outbox`, ...).
- Pure business logic. No `nicegui`, no `sqlalchemy`, no `telethon`, no raw `httpx` imports.
- All I/O goes through `core/*` adapters.
- All public functions are `async def`, take and return Pydantic models from `schemas/`.

## Steps

1. **Schema first.** Add request/result models in `schemas/<domain>.py`. If new Telegram actions are needed, extend `schemas/telegram_actions.py`.
2. **Create the service file.** `services/<domain>.py`:
   - Public async functions taking Pydantic input, returning Pydantic output.
   - Compose with other services freely (`from services.accounts import load_account`).
   - Delegate I/O exclusively to `core/*`.
3. **Use the right gateways:**
   - DB: `from core.db import save_<x>, load_<x>` (or repository module when split lands).
   - Telegram: `from core.telegram_client import execute` + typed action from `schemas/telegram_actions.py`. Wrap non-trivial actions in an outbox row (see `context/telegram.md`).
   - HTTP (Gemini): `from core.<provider> import ...` wrapper.
   - Logging: `from core.logging import log_event`.
   - Config: `from core.config import settings` then `settings.<namespace>.<field>`.
4. **Test — prefer `/tdd`.** `tests/services/test_<domain>.py`:
   - Mock `core/*` adapters (db, telegram_client, http, logging).
   - Cover happy path + at least one failure (validation error, FloodWait, DB rollback).
   - Aim for branch coverage ≥ 90 %.
5. **Run `uv run pytest`.** Strict mode must pass.

## Gotchas

- It is tempting to drop a NiceGUI notification inside a service ("the user clicks 'warm' and gets a toast"). **Don't.** The service returns a result; the feature handler renders the toast. Services must be UI-runnable headless (CLI, test, scheduler).
- It is tempting to import `from features.warming import ...` to reuse warming logic. **Don't.** Pull the shared piece into a service.
- Sync I/O (`time.sleep`, blocking HTTP) freezes the NiceGUI event loop. Use `await` everywhere; for unavoidable sync work, `asyncio.to_thread`.
- A service that grew past ~300 lines or holds multiple unrelated concerns is a smell — split by domain.

## Verify

- [ ] New `services/<domain>.py`; no edit of existing service files unless extending the same domain
- [ ] No `nicegui` / `sqlalchemy` / `telethon` / raw `httpx` imports in the service
- [ ] All public functions are `async def` and take/return Pydantic models
- [ ] Telegram actions go through `core.telegram_client.execute(action)` with a typed action schema
- [ ] Non-trivial Telegram writes go through the outbox, not direct `execute`
- [ ] Config used as `settings.<namespace>.<field>` (no flat `settings.<field>`)
- [ ] `tests/services/test_<domain>.py` exists with mocks of `core/*`; covers happy + failure paths
- [ ] `uv run pytest` passes (strict mode, coverage ≥ 90 %)

## Debug

- Service can't be imported from a test → likely circular import via `core/`. Services must not import `features/`; check that `core/*` doesn't either.
- Test passes but coverage drops below 90 % → unhandled branch in the service; add a failure-path test.
- `FloodWaitError` leaks past the service → the executor should be wrapping it; check `core/telegram_client.execute` returns a `FloodWaitResult`.

## After
Run the GROW step from `ROUTER.md` (update `state/active.md`, touch any out-of-date `context/`, bump `last_updated`).
