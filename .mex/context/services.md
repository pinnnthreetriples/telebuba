---
name: services
description: Business logic layer — services/. Where warming algorithms, FloodWait policy, comment generation, and account state transitions live. Load when writing or reviewing any non-trivial domain logic.
triggers:
  - "service"
  - "business logic"
  - "domain logic"
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
    condition: when adding a new service file
last_updated: 2026-06-10
---

# Services Layer

## What it is

`services/<domain>.py` holds **pure business logic**. The warming algorithm, FloodWait/retry policy, comment generation orchestration, account state transitions — anything that defines *what the system does*, not *how it talks to the world*.

`features/` calls into `services/`. APScheduler jobs registered in `features/warming.py` also call into `services/`. Same code path, no duplication.

## Why it exists

Without `services/`:
- A NiceGUI "Start warming" button calls warming logic in `features/warming.py`.
- An APScheduler job needs the same logic — it lives in `features/warming.py` too, fine.
- A "Run warming once on this account" button in `features/accounts.py` — now we either duplicate the logic, or `features/accounts.py` imports `features/warming.py` (breaks Rule 1).

With `services/warming.py`:
- All three callers import the service. No cross-feature imports. No duplication.
- Tests target the service directly with Pydantic inputs/outputs — no NiceGUI, no scheduler, no Telethon.

## Rules

- **Async only.** Every public function in a service is `async def`.
- **Pydantic at every edge.** Inputs and outputs are models from `schemas/`. No `dict`/`tuple`/`list` of raw values crossing into or out of a service.
- **No I/O directly.** Services delegate I/O to `core/`:
  - DB → `core/db.py` (or a repository module under `core/repositories/`).
  - Telegram → `core/telegram_client.execute(action)` with typed actions from `schemas/telegram_actions.py`.
  - HTTP (Gemini) → `core/<provider>.py` wrapper, not raw `httpx`.
  - Logging → `core/logging.py`.
- **No UI imports.** Never `from nicegui import ...`. Services must be runnable from a CLI / test / scheduler with no UI present.
- **Composition allowed.** A service may import other services. `services/warming.py` may use `services/accounts.py`. This is the one layer where intra-layer imports are fine.
- **One domain per file.** `services/warming.py`, `services/accounts.py`, `services/comments.py`, etc. Not one mega-file.

## What does NOT belong here

- NiceGUI page or component definitions — those are in `features/`.
- SQLAlchemy / Telethon / loguru imports — those live in `core/*`.
- Raw HTTP calls — wrap them in `core/<provider>.py`.
- Long-running blocking work — `await` cooperatively; if CPU-heavy, offload via `asyncio.to_thread`.

## Test policy

- One test file per service in `tests/services/test_<domain>.py`.
- Mock `core/*` adapters (db, telegram_client, http) — services should be testable without touching real I/O.
- Cover happy path + at least one failure path per public function.
- Strict pytest applies: warnings → errors, coverage ≥ 90 %, prefer `/tdd` skill.

## Naming

- File: `services/<domain>.py`. Domain = noun (`accounts`, `warming`, `comments`).
- Functions: snake_case, verb-first (`warm_account`, `compose_comment`, `transition_account_state`).
- No classes for stateless logic. Use them only when the service holds genuine state (rare).

## Example shape (illustrative — not yet implemented)

```python
# services/warming.py
from __future__ import annotations

from core.db import save_warming_run, load_account
from core.telegram_client import execute
from core.logging import log_event
from schemas.warming import WarmingRequest, WarmingResult
from schemas.telegram_actions import JoinChannel


async def warm_account(req: WarmingRequest) -> WarmingResult:
    account = await load_account(req.account_id)
    if not account.is_warming_eligible():
        return WarmingResult(account_id=req.account_id, status="skipped", reason="ineligible")

    action = JoinChannel(channel=req.channel)
    outcome = await execute(account_id=req.account_id, action=action)

    result = WarmingResult(account_id=req.account_id, status=outcome.status, joined=outcome.success)
    await save_warming_run(result)
    log_event(level="INFO", account_id=req.account_id, event="warming_run", result=result.status)
    return result
```

A `features/warming.py` handler calling this is then literally three lines:

```python
async def on_warm_clicked(account_id: int, channel: str) -> None:
    req = WarmingRequest(account_id=account_id, channel=channel)
    result = await services.warming.warm_account(req)
    ui.notify(f"Warming {result.status}")
```
