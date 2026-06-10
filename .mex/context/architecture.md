---
name: architecture
description: How the major pieces of this project connect and flow, plus the exact folder layout and the canonical layer import matrix.
triggers:
  - "architecture"
  - "system design"
  - "how does X connect to Y"
  - "integration"
  - "flow"
  - "folder structure"
  - "layers"
  - "import rules"
edges:
  - target: context/stack.md
    condition: when specific technology details are needed
  - target: context/decisions.md
    condition: when understanding why the architecture is structured this way
  - target: context/conventions.md
    condition: when the question is about coding rules rather than structural rules
  - target: context/telegram.md
    condition: when working with Telethon clients or Telegram interaction
  - target: context/warming.md
    condition: when working with scheduled warming tasks
  - target: context/logging.md
    condition: when working with the three-tier logging architecture
last_updated: 2026-06-10
---

# Architecture

## System Overview

User opens the NiceGUI web interface → clicks a button handled in `features/<name>.py` →
inputs are validated through a Pydantic model from `schemas/` →
state is persisted to SQLite via `core/db.py` (SQLAlchemy) →
a Telethon client is acquired from `core/telegram_client.py` (with per-account proxy via python-socks) for any Telegram side effect →
APScheduler (driven from `features/warming.py`) runs recurring warming tasks against the same clients →
every step emits structured events through `core/logging.py` (loguru file + structlog → SQLite `logs` table + Sentry for prod errors).

Single-process async runtime — NiceGUI hosts the event loop, no separate frontend/backend.

## Folder Structure

```
telebuba/
├── main.py                       NiceGUI entrypoint: starts UI + scheduler, registers features
├── pyproject.toml                uv project + locked deps
├── .env                          secrets (gitignored)
├── .env.example                  template, committed
├── core/                         shared infrastructure — the only layer touching third-party SDKs
│   ├── __init__.py
│   ├── config.py                 pydantic + python-dotenv settings; single source of truth
│   ├── db.py                     SQLAlchemy engine/session + typed helpers; the ONLY place sqlalchemy is imported
│   ├── telegram_client.py        Telethon factory + lifecycle; the ONLY place telethon is imported
│   └── logging.py                loguru + structlog + Sentry; the ONLY place these are imported
├── schemas/                      Pydantic models; shared types, no behavior, no I/O
│   ├── __init__.py
│   └── <domain>.py               one file per domain (accounts.py, warming.py, ...)
├── features/                     user-facing features; one file = one feature
│   ├── __init__.py
│   ├── accounts.py               NiceGUI page + handlers for account management
│   ├── warming.py                NiceGUI page + APScheduler registrations for warming
│   ├── logs.py                   NiceGUI Logs page (polls the SQLite `logs` table)
│   └── <new_feature>.py          new features land as NEW files, never edits of existing ones
└── tests/                        mirrors source tree; pytest
    ├── conftest.py
    ├── core/
    ├── schemas/
    └── features/
        └── test_<feature>.py
```

## Layer Model

Layers, top (UI) to bottom (infrastructure):

```
main.py
  └─ features/*        UI handlers and per-feature business logic
       └─ core/*       shared infrastructure: db, telegram_client, config, logging
            └─ schemas/*    Pydantic models — pure data, no logic, no project imports
```

`schemas/` is shared types, not a downstream layer. All layers may import types; types must not import layers. That is why both `features/` and `core/` may import `schemas/`.

## Allowed Imports

| Layer            | May import                                                                            |
|------------------|---------------------------------------------------------------------------------------|
| `features/*.py`  | `core/*`, `schemas/*`                                                                 |
| `core/*.py`      | `schemas/*`, stdlib, third-party (`sqlalchemy`, `telethon`, `loguru`, `structlog`, `sentry_sdk`, ...) — but NOT `features/*` |
| `schemas/*.py`   | `pydantic`, `typing` only                                                             |
| `tests/*.py`     | anything (verification layer)                                                         |

## Forbidden Imports (each breaks a non-negotiable)

| In…                                          | Must NOT import                                       | Why |
|----------------------------------------------|-------------------------------------------------------|-----|
| any `features/<a>.py`                        | `features/<b>.py` (any other feature)                 | Rule 1 — Feature Isolation |
| any `features/*.py`                          | `sqlalchemy` (any submodule)                          | Rule 6 — DB only via `core/db.py` |
| any `features/*.py`                          | `telethon` (any submodule)                            | Rule 6 — Telegram only via `core/telegram_client.py` |
| any module outside `core/logging.py`         | `loguru`, `structlog`, `sentry_sdk`                   | Rule 4 — logging only via `core/logging.py` |
| any module outside `core/config.py`          | `os.environ`, raw `dotenv`                            | Rule 3 — config only via `core/config.py` |
| `schemas/*`                                  | `core/*`, `features/*`, any I/O library               | Rule 5 — schemas are pure data, no behavior |
| `core/*`                                     | `features/*`                                          | Rule 5 — core does not know about features |

## Data Crossing Layers

Any function whose caller is in a different layer takes and returns Pydantic models from `schemas/`.
Raw `dict`, `tuple`, and `list` of raw values are forbidden as cross-layer parameters or return values.
Inside a single function or module, regular Python data structures are fine.

**Sanity check:** `core/db.py` imports `from schemas.account import AccountResponse` — allowed. `schemas/account.py` does not import `core/db.py` — required. The ORM → Pydantic mapping lives inside `core/db.py`; features receive ready-made schema objects.

## Key Components

- **`core/config.py`** — typed settings (Pydantic + python-dotenv). Single source of truth for tunables and secrets.
- **`core/db.py`** — SQLAlchemy engine/session + helpers. The only path to persistent storage. Takes and returns schemas.
- **`core/telegram_client.py`** — Telethon factory and per-account client lifecycle, wired to per-account proxy via python-socks. The only place Telethon is imported.
- **`core/logging.py`** — loguru file sink + structlog → SQLite `logs` + Sentry init. The only place these are imported.
- **`schemas/*.py`** — Pydantic models flowing between UI, features, core, and DB.
- **`features/*.py`** — one file per user-facing feature; owns its NiceGUI page, handlers, and any scheduler registrations.

## External Dependencies

- **Telegram (via Telethon)** — account creation, warming, posting, commenting. Rate-limited; all calls funnel through `core/telegram_client.py`.
- **SQLite (via SQLAlchemy)** — local file DB sized for ~50 accounts. Single-writer assumption — APScheduler jobs and NiceGUI handlers share one engine through `core/db.py`.
- **Gemini API (via httpx)** — AI comment generation. All calls funnel through a `core/` helper (TBD) so features cannot bypass rate-limiting or key handling.

## What Does NOT Exist Here

- No separate frontend service — NiceGUI is the UI and the server, one process.
- No remote database, queue, or broker — SQLite + APScheduler in-process only.
- No cross-feature imports — shared logic belongs in `core/` or `schemas/`.
- No raw `dict`/`tuple`/`list` passing between layers.
- No third-party SDKs (Telethon, SQLAlchemy, loguru, structlog, sentry_sdk) outside their dedicated `core/` wrapper.
