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
    condition: when adding a new user-facing feature
last_updated: 2026-06-16
---

# Conventions

## Non-Negotiable Rules

Each rule states the rule, then a one-line **Why** where it is not obvious.

### 1. Feature Isolation (UI layer)
- Every feature is its own module or package under `features/`.
- Small feature: `features/<name>.py`. Larger feature: `features/<name>/` with a thin `__init__.py` and cohesive render/helper modules.
- UI-thin only — NiceGUI page/component definitions and click handlers. All business logic delegates to `services/`.
- No imports between feature domains. `features/accounts/**` must not import `features/warming/**`.
- **Why:** keeps feature ownership clear, prevents god-modules, lets agents work on different features with fewer merge conflicts, and keeps UI replaceable.

### 2. Pydantic Boundaries
- All data crossing layers passes through Pydantic models in `schemas/`.
- No raw `dict`, `tuple`, or `list` of raw values as inputs or outputs of cross-layer functions.
- Public functions return Pydantic models or `None`. If returning multiple records, wrap them in a Pydantic response model.
- **Why:** validation at every edge; types for free; prevents drift between UI, core, and DB representations.

### 3. No Hardcoded Values
- No hardcoded paths, limits, API keys, URLs, timings, or thresholds.
- Tunables live in `core/config.py` via nested `pydantic-settings` namespaces.
- Secrets live in `.env` and are read via `core/config.py`.

### 4. Logging Only
- No `print()`.
- All logging through `core/logging.py` (`log_event` for business events; loguru/Sentry encapsulated there).
- See `context/logging.md` for the full logging setup.

### 5. Layer Isolation (4 layers + shared types)
- `features/**` imports `services/*`, `core/*`, `schemas/*` only. Never SQLAlchemy / Telethon / another feature.
- `services/**` imports other `services/*` (composition allowed), `core/*`, `schemas/*` only. Never SQLAlchemy / Telethon directly / `nicegui` / `features/*` / raw provider HTTP clients.
- `core/**` imports `schemas/*` (shared types), stdlib, and third-party SDKs. Never `services/*` or `features/*`.
- `schemas/*.py` imports only `pydantic`, `typing`, and safe stdlib typing helpers.
- **Why:** `schemas/` is a shared-types layer, not a downstream layer — all layers may import types; types must not import layers. The `services/` layer keeps business logic UI-agnostic and reusable.
- Full import matrix: `context/architecture.md`.

### 6. Database & Telegram Client Gateways
- Database access only through `core/db.py` compatibility re-exports or `core/repositories/<aggregate>.py`.
- Telegram actions only through `core.telegram_client.execute(account_id, action)` — `action` is a Pydantic class from `schemas/telegram_actions.py`.
- `sqlalchemy` and `telethon` must not be imported in `services/**` or `features/**`.
- **Why:** one place to enforce session lifecycle, typed results, proxy config, error classification, and logging. Declarative actions are testable without mocking Telethon across the app.

### 7. Test Coverage (maximum strictness — prefer `/tdd` skill)
- Every new feature/service change ships with tests.
- Tests run via `pytest`. Green tests = done. Strictness is configured, not optional — see `pyproject.toml [tool.pytest.ini_options]`.
- Hard pytest policy:
  - `filterwarnings = ["error"]` — any warning is a test failure.
  - `--strict-markers` / `--strict-config` — undefined markers or config typos fail the run.
  - `xfail_strict = true` — an `xfail` that passes is a failure.
  - `asyncio_mode = "strict"` — every async test must be explicitly marked.
  - `--cov-fail-under=90` with `--cov-branch` — branch coverage must stay ≥ 90% on `core`, `features`, `schemas`, `services`.
- Adding a `filterwarnings` ignore requires a one-line justification comment naming the upstream library and the reason it cannot be fixed at the source. Blanket ignores are forbidden.
- **Why:** warnings-as-errors and branch coverage keep the quality floor honest.

### 8. Async & Type Safety
- All functions have type hints. `from __future__ import annotations` at the top of every Python file.
- Every DB, Telegram, or HTTP operation is `async def` at the public boundary.
- Exceptions: `raise CustomError(...) from e` when wrapping another exception.

### 9. Device Fingerprint Immutable
- One device profile per account, created at registration and never mutated.
- **Why:** it is identity metadata; changing it mid-life creates inconsistent account history.

### 10. Configuration-Driven
- All parameters come from `core/config.py`.
- `core/config.py` uses nested namespaces — `settings.warming`, `settings.gemini`, `settings.telegram`, etc. Each domain owns its slice; no one mega-`Settings` blob.
- No magic numbers in code.

### 11. Services Layer
- All business logic — runtime workflows, state transitions, account operations, content generation orchestration — lives in `services/<domain>/` or `services/<domain>.py`. NOT in `features/` and NOT in `core/`.
- Services are async, take and return Pydantic models, and may compose freely with other services.
- A feature handler validates input, calls a service, then renders the result. Anything more is a signal to move logic into a service.
- **Why:** business logic must be callable from UI, tests, scripts, and runtime tasks without duplicating it or importing one feature from another.

## Pre-Commit Checklist

Run this checklist explicitly before presenting any code or committing:

- [ ] No imports between feature domains under `features/`?
- [ ] Business logic lives in `services/`, NOT in `features/`?
- [ ] No `sqlalchemy` / `telethon` import outside `core/*`?
- [ ] Telegram actions go through `core.telegram_client.execute(account_id, action)`, not raw SDK calls?
- [ ] Every function has type hints?
- [ ] Every public cross-layer function returns a Pydantic model or `None`?
- [ ] No `print()`?
- [ ] No hardcoded values?
- [ ] Config via `settings.<namespace>.<field>`, not flat `settings.<field>`?
- [ ] Test exists for the changed feature/service/core path?
- [ ] `uv run pytest` passes with zero warnings and coverage ≥ 90%?

## Naming

- Files and packages: snake_case (`account_creation.py`, not `AccountCreation.py`).
- Functions: snake_case, verb-first (`create_account`, not `account_creator`).
- Pydantic models: PascalCase, suffix by role (`AccountCreate`, `AccountRead`, `AccountUpdate`, `AccountResponse`).
- DB tables / columns: snake_case (`accounts`, `created_at`).
- Feature domains: `features/<name>.py` for small pages, `features/<name>/` for larger pages.
- Service domains: `services/<domain>.py` for small domains, `services/<domain>/` for larger domains.

## Structure

- One domain per module/package. Do not keep adding unrelated concerns to a package root.
- Package root (`__init__.py`) should be thin: page scaffold, public API re-exports, or compatibility shims only.
- Shared business logic lives in `services/`. Shared infrastructure lives in `core/`. Shared contracts live in `schemas/`.
- Tests live in `tests/`, mirroring the source tree where practical (`features/accounts/` → `tests/features/test_accounts*.py`).
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

**DB via repositories / core DB gateway, not SQLAlchemy in features.**

```python
# Correct — in a service or core-facing helper
from core.db import fetch_account
account = await fetch_account(account_id)

# Wrong — in features/ or services/
from sqlalchemy.orm import Session
session.add(AccountModel(...))
```

**Telegram via the typed executor, never Telethon outside `core/`.**

```python
# Correct — build a typed action, hand it to the executor
from core.telegram_client import execute
from schemas.telegram_actions import UpdateProfile
result = await execute(account_id, UpdateProfile(first_name="...", last_name=None, username=None, bio=None))

# Wrong — raw Telethon outside core/
from telethon import TelegramClient
client = TelegramClient(...)
```
