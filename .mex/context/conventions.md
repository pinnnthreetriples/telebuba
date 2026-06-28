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
  - target: context/frontend.md
    condition: when the code lives in frontend/ (the SPA has its own law)
  - target: context/stack.md
    condition: when a convention is tied to a specific library
  - target: patterns/add-api-endpoint.md
    condition: when adding a backend endpoint
  - target: patterns/add-frontend-slice.md
    condition: when adding a frontend screen/widget/entity
last_updated: 2026-06-28
---

# Conventions

These non-negotiables govern the **Python backend** (`api/`, `services/`, `core/`, `schemas/`).
The React SPA under `frontend/` has its own law — see `context/frontend.md`. Rules that are
explicitly backend-only say so.

## Non-Negotiable Rules

Each rule states the rule, then a one-line **Why** where it is not obvious.

### 1. API Layer is UI-thin (`api/`)
- The top backend layer is `api/` — versioned `/api/v1` FastAPI routers, one module/package
  per domain. (`api/` replaces the deleted NiceGUI `features/` layer.)
- A route handler does exactly three things: **validate** input (Pydantic), **call a service**,
  **serialize** the result. No business logic, no DB/Telegram access.
- `api/` may import **only** `services/`, `schemas/`, `core.config`, `core.logging`, and
  `fastapi`. Nothing else from `core/` — not `core.db`, `core.repositories`,
  `core.telegram_client`, `core.gemini`. Enforced by `tests/test_architecture.py`.
- **Why:** keeps routes thin and the API replaceable, and holds the same gateway discipline
  `features/` had (data only via services) so services stay reusable from scripts, tasks, and tests.

### 2. Pydantic Boundaries
- All data crossing layers passes through Pydantic models in `schemas/`.
- No raw `dict`, `tuple`, or `list` of raw values as inputs or outputs of cross-layer functions.
- Public functions return Pydantic models or `None`. Multi-item returns use a wrapper model
  (use the generic `Page[T]` from `schemas/api.py` for cursor-paginated lists).
- **Why:** validation at every edge; types for free; the contract that becomes OpenAPI → the
  generated TS client.

### 3. No Hardcoded Values (backend)
- No hardcoded paths, limits, API keys, URLs, timings, or thresholds in Python code.
- Tunables live in `core/config.py` via nested `pydantic-settings` namespaces.
- Secrets live in `.env` and are read via `core/config.py`.
- **Frontend counterpart:** the SPA gets build/runtime config from Vite env vars
  (`import.meta.env.VITE_*`), never hardcoded — see `context/frontend.md`.

### 4. Logging Only
- No `print()`.
- All logging through `core/logging.py` (`log_event` for business events; loguru/Sentry
  encapsulated there). See `context/logging.md`.

### 5. Layer Isolation (4 backend layers + shared types)
- `api/` imports `services/`, `schemas/`, `core.config`, `core.logging`, `fastapi` only.
  Never `core.db` / `core.repositories` / `core.telegram_client` / `core.gemini` /
  `sqlalchemy` / `telethon` / another transport.
- `services/` imports other `services/` (composition allowed), `core/`, `schemas/` only.
  Never SQLAlchemy / Telethon directly / `fastapi` / raw provider HTTP clients.
- `core/` imports `schemas/` (shared types), stdlib, and third-party SDKs. Never `services/`
  or `api/`.
- `schemas/` imports only `pydantic`, `typing`, and safe stdlib typing helpers.
- The frontend is a separate tree reaching the backend only over `/api/v1`.
- **Why:** `schemas/` is a shared-types side-band, not a downstream layer — all layers may
  import types; types must not import layers. `services/` stays transport-agnostic and reusable.
- Full import matrix: `context/architecture.md`.

### 6. Database & Telegram Client Gateways
- Database access only through `core/db.py` compatibility re-exports or
  `core/repositories/<aggregate>.py`.
- Telegram actions only through `core.telegram_client.execute(account_id, action)` — `action`
  is a Pydantic class from `schemas/telegram_actions.py`.
- `sqlalchemy` and `telethon` must not be imported in `services/` or `api/`.
- **Why:** one place to enforce session lifecycle, typed results, proxy config, error
  classification, and logging.

### 7. Test Coverage (maximum strictness — prefer `/tdd` skill)
- Every new endpoint/service/core change ships with tests.
- Tests run via `pytest`. Strictness is configured, not optional — see
  `pyproject.toml [tool.pytest.ini_options]`.
- Hard pytest policy:
  - `filterwarnings = ["error"]` — any warning is a test failure.
  - `--strict-markers` / `--strict-config` — undefined markers or config typos fail the run.
  - `xfail_strict = true` — an `xfail` that passes is a failure.
  - `asyncio_mode = "strict"` — every async test must be explicitly marked.
  - `--cov-fail-under=90` with `--cov-branch` — branch coverage ≥ 90% on `api`, `core`,
    `schemas`, `services`.
- Adding a `filterwarnings` ignore requires a one-line justification comment naming the
  upstream library and why it cannot be fixed at the source. Blanket ignores are forbidden.
- **Frontend** has its own test floor (Vitest ≥ 80% + Playwright smoke) — see `context/frontend.md`.

### 8. Async & Type Safety
- All functions have type hints. `from __future__ import annotations` at the top of every Python file.
- Every DB, Telegram, or HTTP operation is `async def` at the public boundary.
- Exceptions: `raise CustomError(...) from e` when wrapping another exception.

### 9. Device Fingerprint Immutable
- One device profile per account, created at registration and never mutated.
- **Why:** it is identity metadata; changing it mid-life creates inconsistent account history.

### 10. Configuration-Driven (backend)
- All backend parameters come from `core/config.py`.
- `core/config.py` uses nested namespaces — `settings.warming`, `settings.gemini`,
  `settings.telegram`, `settings.api`, `settings.auth`, etc. Each domain owns its slice.
- No magic numbers in code. (Frontend config: `VITE_*`, see rule #3.)

### 11. Services Layer
- All business logic — runtime workflows, state transitions, account operations, auth policy,
  content generation orchestration — lives in `services/<domain>/` or `services/<domain>.py`.
  NOT in `api/` and NOT in `core/`.
- Services are async, take and return Pydantic models, and may compose freely with other services.
- A route handler validates input, calls a service, then serializes the result. Anything more
  is a signal to move logic into a service.
- **Why:** business logic must be callable from the API, tests, scripts, and runtime tasks
  without duplicating it.

## Pre-Commit Checklist

Run this checklist explicitly before presenting any code or committing:

- [ ] `api/` route handlers are thin: validate → call service → serialize?
- [ ] No business logic in `api/`; no `core.db`/`core.repositories`/`core.telegram_client`/
      `sqlalchemy`/`telethon` import in `api/`?
- [ ] Business logic lives in `services/`, NOT in `api/`?
- [ ] No `sqlalchemy` / `telethon` import outside `core/`?
- [ ] Telegram actions go through `core.telegram_client.execute(account_id, action)`?
- [ ] Every function has type hints?
- [ ] Every public cross-layer function returns a Pydantic model or `None` (lists via `Page[T]`)?
- [ ] No `print()`?
- [ ] No hardcoded values; config via `settings.<namespace>.<field>` (backend) / `VITE_*` (frontend)?
- [ ] Responses are locale-neutral (codes/enums + ISO timestamps), no pre-translated text?
- [ ] Test exists for the changed endpoint/service/core path?
- [ ] `uv run pytest` passes with zero warnings and coverage ≥ 90%?
- [ ] (Frontend) boundary-lint / tsc / eslint / vitest pass — see `context/frontend.md`?

## Naming

- Files and packages: snake_case (e.g. `account_creation`, not `AccountCreation`).
- Functions: snake_case, verb-first (`create_account`, not `account_creator`).
- Pydantic models: PascalCase, suffix by role (`AccountCreate`, `AccountRead`, `AccountUpdate`,
  `AccountResponse`).
- DB tables / columns: snake_case (`accounts`, `created_at`).
- API route modules: `api/v1/<domain>.py` (or a package once it grows).
- Service domains: `services/<domain>.py` for small domains, `services/<domain>/` for larger.
- Frontend naming + structure: `context/frontend.md` (FSD slices/segments, `index.ts` public API).

## Structure

- One domain per module/package. Do not keep adding unrelated concerns to a package root.
- Package root (`__init__.py`) should be thin: app/router assembly, public API re-exports, or
  compatibility shims only.
- Shared business logic lives in `services/`. Shared infrastructure lives in `core/`. Shared
  contracts live in `schemas/`.
- Tests live in `tests/`, mirroring the source tree where practical (e.g. `api/v1/accounts.py`
  → `tests/api/test_accounts.py`).
- Full layer / import rules: `context/architecture.md`.

## File Placement Guide

The non-negotiables above say *which layer* code belongs in. This section says *which file
inside that layer* it belongs in, so successive refactors don't keep undoing each other.

### When to create a new file

Create a new file when:

- the new code owns a distinct concern;
- the existing file is already ~150–200 lines and growing;
- the existing file mixes routing, business logic, adapters, or schemas in one place;
- the new code will be tested in isolation;
- the new code is used by several functions inside the same domain package.

### Where each kind of code goes

- **`api/` — FastAPI routers only.**
  Route definitions, request/response binding, dependency wiring (`Depends`), error mapping.
  No business decisions, no DB/Telegram access.
  Recommended layout:
  - `__init__.py` — app factory / router assembly
  - `v1/<domain>.py` (or `v1/<domain>/` once it grows) — routes for one domain
  - `deps.py` — shared dependencies (`get_current_user`, pagination params)
  - `errors.py` — error-envelope mapping
- **`services/<domain>/` — business logic.**
  State transitions, validation beyond request shape, orchestration, account/session/proxy/
  profile/domain operations, auth policy, runtime workflows. Calls `core/` gateways. Returns
  Pydantic models.
- **`core/` — infrastructure only.**
  DB metadata + repositories, Telegram gateway, Gemini/HTTP gateways, auth primitives (hash/JWT),
  config, logging, migrations, adapters around external SDKs.
- **`schemas/` — Pydantic contracts only.**
  Request/response models, the error envelope + `Page[T]` (`schemas/api.py`), typed action
  models, enums/literals. No I/O, no service/core/api imports.
- **`frontend/` — the React SPA.** Placement follows FSD (`context/frontend.md`): slices live
  under `app/routes/pages/widgets/features/entities/shared`, each with a public `index.ts`.
- **`tests/`** — mirror the source tree where practical:
  - `api/v1/accounts.py` → `tests/api/test_accounts.py`
  - `services/accounts/sessions.py` → `tests/services/test_accounts.py`
  - architecture rules → `tests/test_architecture.py`

### Package-root rule

`__init__.py` should stay thin: app/router assembly, public re-export, or compatibility shim
only. Do **NOT** put growing business logic or large route blocks into `__init__.py`.

### Split triggers

Split a file when any of these holds:

- it has more than one reason to change;
- it mixes routing with business orchestration;
- it mixes service orchestration with low-level adapter calls;
- tests need to monkeypatch internal collaborators (each seam lives on its owning submodule);
- radon / aislop / maintainability gates start pushing back;
- future agents would likely edit unrelated parts of the same file.

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

**Thin API route — validate → call service → serialize.**

```python
# Correct — api/v1/accounts.py
@router.get("/accounts", response_model=Page[AccountRead])
async def list_accounts(cursor: str | None = None) -> Page[AccountRead]:
    return await services.accounts.list_accounts(cursor=cursor)

# Wrong — business logic / DB access in the route
@router.get("/accounts")
async def list_accounts():
    rows = session.execute(select(AccountModel))  # SQLAlchemy in api/ — forbidden
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

**DB via repositories / core DB gateway, not SQLAlchemy in `api/` or `services/`.**

```python
# Correct — in a service
from core.db import fetch_account
account = await fetch_account(account_id)

# Wrong — in api/ or services/
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
</content>
