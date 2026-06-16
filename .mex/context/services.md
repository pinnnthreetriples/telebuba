---
name: services
description: Business logic layer — services/. Load when writing or reviewing non-trivial domain logic, runtime workflows, state transitions, account operations, or generation orchestration.
triggers:
  - "service"
  - "business logic"
  - "domain logic"
  - "runtime workflow"
  - "warming algorithm"
  - "comment generation"
  - "state transition"
edges:
  - target: context/architecture.md
    condition: when placing service code in the right layer or checking imports
  - target: context/conventions.md
    condition: when the question is the rule (UI-thin, no SDK imports, Pydantic at edges)
  - target: context/telegram.md
    condition: when the service drives Telegram actions through the executor
  - target: patterns/add-service.md
    condition: when adding a new service module/package
last_updated: 2026-06-16
---

# Services Layer

## What it is

`services/<domain>.py` or `services/<domain>/` holds **pure business logic**: state transitions, runtime workflow orchestration, account operations, content generation orchestration, readiness checks, and policy decisions — anything that defines *what the system does*, not *how it talks to the world*.

`features/` calls into `services/`. Runtime tasks also call into services. Same code path, no duplication.

## Current service domains

- `services/accounts/` — account lifecycle/actions, session import/check, proxy operations, profile/media actions, account table read model helpers.
- `services/warming/` — runtime workflow domain package: channels, settings storage, board read model, pacing/readiness, cycle execution, runtime task ownership.
- `services/trust.py` — trust-score calculation from stored account signals.
- `comments` domain — not yet started.

## Why it exists

Without `services/`:
- UI handlers become the home of business logic.
- Runtime tasks need the same logic and either duplicate it or import a feature.
- A second feature needing the same behavior creates a cross-feature import.

With `services/`:
- All callers import the service.
- No cross-feature imports.
- Tests target service functions directly with mocked `core/*` adapters.
- Domain logic remains independent from NiceGUI.

## Rules

- **Async public API.** Public service functions that perform I/O are `async def`.
- **Pydantic at every edge.** Inputs and outputs are models from `schemas/`. No raw `dict`/`tuple`/`list` of raw values crossing into or out of a service.
- **No SDK I/O directly.** Services delegate I/O to `core/`:
  - DB → `core/db.py` compatibility re-exports or `core/repositories/`.
  - Telegram → `core.telegram_client.execute(account_id, action)` with typed actions from `schemas/telegram_actions.py`.
  - HTTP providers → `core/<provider>.py` wrapper, not raw `httpx`.
  - Logging → `core/logging.py`.
- **No UI imports.** Never `from nicegui import ...`. Services must be runnable from tests/scripts/runtime without UI present.
- **Composition allowed.** A service may import other services. This is the one layer where intra-layer imports are fine.
- **One domain per module/package.** Small domain: `services/<domain>.py`. Large domain: `services/<domain>/` with a thin `__init__.py` and focused submodules.

## Package root rule

A service package root (`services/<domain>/__init__.py`) should be thin:
- public API re-exports,
- small compatibility shim,
- or minimal orchestration that intentionally keeps test seams patchable.

If the root starts collecting unrelated logic, split into focused submodules (e.g. `_runtime`, `settings_store`, `board`, `pacing`) and re-export the public API.

## What does NOT belong here

- NiceGUI page/component definitions — those are in `features/`.
- SQLAlchemy / Telethon / loguru imports — those live in `core/*`.
- Raw HTTP calls — wrap them in `core/<provider>.py`.
- Long-running blocking work — `await` cooperatively; if CPU-heavy, offload via `asyncio.to_thread`.

## Test policy

- Service tests live under `tests/services/` and may be split by subdomain for packages.
- Mock `core/*` adapters (db, telegram_client, http, logging) — services should be testable without touching real I/O.
- Cover happy path + failure paths for public functions.
- Strict pytest applies: warnings → errors, branch coverage ≥ 90%, prefer `/tdd` skill.

## Naming

- File/package: `services/<domain>.py` or `services/<domain>/`. Domain = noun (`accounts`, `warming`, `comments`).
- Functions: snake_case, verb-first (`create_account`, `compose_comment`, `transition_account_state`).
- No classes for stateless logic. Use them only when the service holds genuine state.

## Example shape

```python
from __future__ import annotations

from core.db import fetch_account
from core.logging import log_event
from core.telegram_client import execute
from schemas.accounts import AccountRead
from schemas.telegram_actions import UpdateProfile


async def rename_account(account_id: str, first_name: str) -> AccountRead:
    account = await fetch_account(account_id)
    if account is None:
        msg = f"unknown account: {account_id}"
        raise ValueError(msg)

    result = await execute(account_id, UpdateProfile(first_name=first_name, last_name=None, username=None, bio=None))
    await log_event("INFO", "account_rename_requested", account_id=account_id, extra={"status": result.status})
    return account
```

A feature handler should then only validate input, call the service, and render/notify.
