---
name: conventions
description: How code is written in this project — non-negotiable rules with rationale, pre-commit checklist, naming, structure, patterns. Load when writing or reviewing any code.
triggers:
  - "convention"
  - "pattern"
  - "naming"
  - "style"
  - "how should I"
  - "what's the right way"
  - "rules"
  - "non-negotiable"
edges:
  - target: context/architecture.md
    condition: when the question is about layer structure or the import matrix
  - target: context/stack.md
    condition: when a convention is tied to a specific library
  - target: patterns/add-feature.md
    condition: when adding a new feature file
last_updated: 2026-06-10
---

# Conventions

## Non-Negotiable Rules

Each rule states the rule, then a one-line **Why** where it is not obvious.

### 1. Feature Isolation (UI layer)
- Every feature is its own file in `features/`. **UI-thin** — NiceGUI page + click handlers only. All business logic delegates to `services/`.
- Existing feature files are never modified to add behavior — create a new file.
- No imports between `features/*.py`. `features/accounts.py` must not import `features/warming.py`.
- **Why:** keeps "add a feature" a pure additive change; prevents the slow drift of a god-module; lets agents work on different features without merge conflicts. Thin UI means we can swap NiceGUI for CLI/REST later without rewriting domain logic.

### 2. Pydantic Boundaries
- All data crossing layers passes through Pydantic models in `schemas/`.
- No raw `dict`, `tuple`, or `list` of raw values as inputs or outputs of cross-layer functions.
- Public functions return Pydantic models or `None`.
- **Why:** validation at every edge; types for free; prevents drift between UI, core, and DB representations.

### 3. No Hardcoded Values
- No hardcoded paths, limits, API keys, URLs.
- Tunables live in `core/config.py` (Pydantic + python-dotenv).
- Secrets live in `.env` and are read via `core/config.py`.

### 4. Logging Only
- No `print()`.
- All logging through `core/logging.py` (loguru + SQLite `logs` table; Sentry in prod).
- See `context/logging.md` for the full three-tier setup.

### 5. Layer Isolation (4 layers + shared types)
- `features/*.py` imports `services/*`, `core/*`, `schemas/*` only. Never SQLAlchemy / Telethon / another feature.
- `services/*.py` imports other `services/*` (composition allowed), `core/*`, `schemas/*` only. Never SQLAlchemy / Telethon directly / `nicegui` / `features/*`.
- `core/*.py` imports `schemas/*` (shared types), stdlib, and third-party SDKs. Never `services/*` or `features/*`.
- `schemas/*.py` imports only `pydantic` and `typing`.
- **Why:** `schemas/` is a shared-types layer, not a downstream layer — all layers may import types; types must not import layers. The `services/` layer is what makes business logic UI-agnostic and reusable from both NiceGUI handlers and APScheduler jobs.
- Full import matrix: `context/architecture.md`.

### 6. Database & Telegram Client Gateways
- Database access only through `core/db.py` (splits into `core/repositories/<aggregate>.py` once tables ≥ 5).
- Telegram actions only through `core/telegram_client.execute(action)` — `action` is a Pydantic class from `schemas/telegram_actions.py` (e.g. `JoinChannel`, `PostComment`, `UpdateProfile`). No direct `client.send_message(...)` from services or features.
- `sqlalchemy` and `telethon` must not be imported in `services/*.py` or `features/*.py`.
- **Why:** one place to enforce session lifecycle, rate limits, proxy config, FloodWait handling, and outbox/retry policy. Declarative actions are also testable without mocking Telethon.

### 7. Test Coverage (maximum strictness — prefer `/tdd` skill)
- Every new feature ships with `tests/test_*.py` using fixtures from `conftest.py`.
- Tests run via `pytest`. Green tests = done. **Strictness is configured, not optional** — see `pyproject.toml [tool.pytest.ini_options]`.
- Hard pytest policy (enforced by config):
  - `filterwarnings = ["error"]` — **any** warning is a test failure.
  - `--strict-markers` / `--strict-config` — undefined markers or config typos fail the run.
  - `xfail_strict = true` — an `xfail` that passes is a failure.
  - `asyncio_mode = "strict"` — every async test must be explicitly marked.
  - `--cov-fail-under=90` with `--cov-branch` — branch coverage must stay ≥ 90% on `core`, `features`, `schemas`.
- Hypothesis runs the `strict` profile by default (200 examples, full reproducer blob, every bug surfaced). The `dev` profile (50 examples) is for the fast inner loop only — never for CI.
- **Adding a `filterwarnings` ignore requires a one-line justification comment** naming the upstream library and the reason it cannot be fixed at the source. Blanket ignores are forbidden.
- **Why:** every warning is a real signal someone chose to surface. Once we start ignoring "harmless" ones, real ones disappear into the noise. Treating warnings as errors keeps the floor honest.

### 8. Async & Type Safety
- All functions have type hints. `from __future__ import annotations` at the top of every file.
- Every DB, Telegram, or HTTP operation is `async def`.
- Exceptions: `raise CustomError(...) from e` (preserve the cause chain).

### 9. Device Fingerprint Immutable
- One device profile per account, created at registration and never mutated.
- **Why:** Telegram correlates device fingerprint with account history; mutating a profile mid-life is a strong ban signal. Treat the profile as an immutable identity attribute, not a settings blob.

### 10. Configuration-Driven
- All parameters (limits, delays, proxy settings) come from `core/config.py`.
- `core/config.py` uses **nested namespaces** — `settings.warming`, `settings.gemini`, `settings.telegram`, etc. Each domain owns its slice; no one mega-`Settings` blob.
- No magic numbers in code.

### 11. Services Layer (NEW)
- All business logic — warming algorithm, FloodWait/retry policy, comment generation, account state transitions — lives in `services/<domain>.py`. NOT in `features/` (UI-thin) and NOT in `core/` (infra-only).
- Services are async, take and return Pydantic models, and may compose freely with other services.
- A feature handler is a 3-liner: validate input, call a service, render result. Anything longer = logic leaking out of the service.
- **Why:** business logic must be callable from both NiceGUI handlers (`features/`) AND APScheduler jobs (`features/warming.py` registrations) without duplication. If logic lives in `features/warming.py`, calling it from `features/accounts.py` would require a cross-feature import (Rule 1 violation). Services solve this cleanly.

## Pre-Commit Checklist

Run this checklist explicitly before presenting any code or committing:

- [ ] No imports between `features/*.py`?
- [ ] Business logic lives in `services/`, NOT in `features/` (handlers stay UI-thin)?
- [ ] No `sqlalchemy` / `telethon` import outside `core/*`?
- [ ] Telegram actions go through `core/telegram_client.execute(action)`, not raw method calls?
- [ ] Every function has type hints?
- [ ] Every public function returns a Pydantic model (or `None`)?
- [ ] No `print()`?
- [ ] No hardcoded values?
- [ ] Config via `settings.<namespace>.<field>`, not flat `settings.<field>`?
- [ ] Test exists for the new feature AND the new service?
- [ ] `uv run pytest` passes with zero warnings and coverage ≥ 90%?

## Naming

- Files: snake_case (`account_creation.py`, not `AccountCreation.py`)
- Functions: snake_case, verb-first (`create_account`, not `account_creator`)
- Pydantic models: PascalCase, suffix by role (`AccountCreate`, `AccountRead`, `AccountUpdate`, `AccountResponse`)
- DB tables / columns: snake_case (`accounts`, `created_at`)
- Feature files: one feature = one file in `features/`, named after the feature (`features/accounts.py`, `features/warming.py`)

## Structure

- One feature per file in `features/`. Never edit an existing feature file when adding a new feature.
- Shared logic lives in `core/`. If two features need it, it belongs there, not duplicated.
- Tests live in `tests/`, mirroring the source tree (`features/accounts.py` → `tests/features/test_accounts.py`).
- Full layer / import rules: `context/architecture.md`.

## Code Patterns

**Pydantic at boundaries.**

```python
# Correct
async def create_account(data: AccountCreate) -> AccountRead:
    ...

# Wrong
async def create_account(data: dict) -> dict:
    ...
```

**Config via `core/config.py`.**

```python
# Correct — nested namespaces, one per domain
from core.config import settings
session_path = settings.telegram.session_dir / f"{account_id}.session"

# Wrong
session_path = f"./sessions/{account_id}.session"
```

**DB via `core/db.py`, not SQLAlchemy in features.**

```python
# Correct — in features/accounts.py
from core.db import save_account
saved = await save_account(account_create)

# Wrong — in features/accounts.py
from sqlalchemy.orm import Session
session.add(AccountModel(...))
```

**Telegram via the typed executor, never Telethon outside `core/`.**

```python
# Correct — build a typed action, hand it to the executor (usual home: a service)
from core.telegram_client import execute
from schemas.telegram_actions import UpdateProfile
result = await execute(account_id, UpdateProfile(first_name="...", last_name=None, username=None, bio=None))

# Wrong — raw Telethon, or any client.send_message(...), outside core/
from telethon import TelegramClient
client = TelegramClient(...)
```
