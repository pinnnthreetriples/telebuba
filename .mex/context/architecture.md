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

User opens the NiceGUI web interface → clicks a button in `features/<name>.py` (thin UI handler) →
inputs validated through a Pydantic model from `schemas/` →
handler delegates to `services/<domain>.py` (business logic — warming algorithm, FloodWait policy, state transitions) →
service uses `core/*` for I/O: `core/db.py` for SQLite, `core/telegram_client.py` for Telethon (with per-account proxy via python-socks), `core/logging.py` for events →
APScheduler runs the same services on a schedule, without going through the UI →
every step emits structured events (loguru file + structlog → SQLite `logs` table + Sentry for prod errors).

Single-process async runtime — NiceGUI hosts the event loop, no separate frontend/backend.

## Folder Structure

```
telebuba/
├── main.py                       NiceGUI entrypoint: starts UI, registers features
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
│   └── <domain>.py               one file per domain (accounts.py, warming.py, telegram_actions.py, ...)
├── services/                     business logic — pure, reusable, no UI, no I/O directly (planned)
│   ├── __init__.py
│   └── <domain>.py               warming algorithm, FloodWait policy, comment generation, ... — callable from features AND scheduler
├── features/                     UI-thin handlers; one file = one feature; delegates business logic to services/
│   ├── __init__.py
│   ├── accounts.py               NiceGUI accounts page — currently does its own orchestration (predates services/, refactor planned)
│   └── <feature>.py              NEW files only — never edits of existing ones
└── tests/                        mirrors source tree; pytest
    ├── conftest.py
    ├── core/
    ├── schemas/
    └── features/
        └── test_<feature>.py
```

## Layer Model

Four layers, top (UI) to bottom (infra). `schemas/` is the shared types side-band, not a downstream layer.

```
main.py
  └─ features/*        UI-thin handlers (NiceGUI page + click handlers). NO business logic.
       └─ services/*   business logic — warming, FloodWait policy, comment generation, etc.
            └─ core/*  infrastructure: db, telegram_client, config, logging
                 └─ schemas/*    Pydantic models — pure data, no logic, no project imports
```

`schemas/` is shared types, not a downstream layer. All layers may import types; types must not import layers.

## Allowed Imports

| Layer            | May import                                                                            |
|------------------|---------------------------------------------------------------------------------------|
| `features/*.py`  | `services/*`, `core/*`, `schemas/*`. UI logic ONLY — business logic goes to `services/`. |
| `services/*.py`  | other `services/*`, `core/*`, `schemas/*`. Composition between services is allowed.   |
| `core/*.py`      | `schemas/*`, stdlib, third-party (`sqlalchemy`, `telethon`, `loguru`, `structlog`, `sentry_sdk`, ...). NOT `features/*` or `services/*`. |
| `schemas/*.py`   | `pydantic`, `typing` only.                                                            |
| `tests/*.py`     | anything (verification layer).                                                        |

## Forbidden Imports (each breaks a non-negotiable)

| In…                                          | Must NOT import                                       | Why |
|----------------------------------------------|-------------------------------------------------------|-----|
| any `features/<a>.py`                        | `features/<b>.py` (any other feature)                 | Rule 1 — Feature Isolation |
| any `features/*.py`                          | `sqlalchemy`, `telethon`                              | Rule 6 — gateways only via `core/*` |
| any `services/*.py`                          | `sqlalchemy`, `telethon`                              | Rule 6 — services use `core/` adapters, never the SDK |
| any `services/*.py`                          | `nicegui`, anything UI-specific                       | Rule 11 — services are UI-agnostic |
| any module outside `core/logging.py`         | `loguru`, `structlog`, `sentry_sdk`                   | Rule 4 — logging only via `core/logging.py` |
| any module outside `core/config.py`          | `os.environ`, raw `dotenv`                            | Rule 3 — config only via `core/config.py` |
| `schemas/*`                                  | `core/*`, `services/*`, `features/*`, any I/O library | Rule 5 — schemas are pure data, no behavior |
| `core/*`                                     | `services/*`, `features/*`                            | Rule 5 — core does not know about services or features |

Rule numbers reference the canonical list in `context/conventions.md`. AGENTS.md mirrors the same 11 rules with the same numbering.

## Data Crossing Layers

Any function whose caller is in a different layer takes and returns Pydantic models from `schemas/`.
Raw `dict`, `tuple`, and `list` of raw values are forbidden as cross-layer parameters or return values.
Inside a single function or module, regular Python data structures are fine.

**Sanity check:** `core/db.py` imports `from schemas.account import AccountResponse` — allowed. `schemas/account.py` does not import `core/db.py` — required. The ORM → Pydantic mapping lives inside `core/db.py`; features receive ready-made schema objects.

## Key Components

- **`main.py`** — registers NiceGUI pages and runs the app on `settings.ui_port`.
- **`core/config.py`** — typed settings (Pydantic + python-dotenv). Nested namespaces per feature (`settings.warming`, `settings.gemini`, ...). Single source of truth for tunables and secrets.
- **`core/db.py`** — SQLAlchemy engine/session + helpers. Currently persists `accounts` and `device_fingerprints`; more tables coming. Splits into `core/repositories/<aggregate>.py` once tables ≥ 5.
- **`core/telegram_client.py`** — Telethon factory + `execute(action: BaseAction)` entry point (planned). Per-account proxy via python-socks. Action types are Pydantic schemas (`schemas/telegram_actions.py`), not raw method calls. Currently exposes client construction + `check_telegram_session()`.
- **`core/tdata_import.py`** — opentele2 adapter. Only place opentele2 is imported. Safe-extracts `tdata.zip` and writes Telethon `.session` files.
- **`features/accounts.py`** — NiceGUI accounts page: table, filters, add-account dialog with `.session` and `tdata.zip` upload, session-check actions. Predates the services/ layer.
- **`core/logging.py`** — loguru file sink + structlog → SQLite `logs` + Sentry init. The only place these are imported.
- **`schemas/*.py`** — Pydantic models flowing between UI, services, core, and DB. Also: `schemas/telegram_actions.py` declares each Telegram action as a typed Pydantic class.
- **`services/*.py`** — pure business logic: warming algorithm, FloodWait/retry policy, comment generation orchestration. Called from `features/` (UI) and from `features/warming.py`'s scheduler registrations. UI-agnostic.
- **`features/*.py`** — UI-thin: NiceGUI page + click handlers. Delegates all logic to services. Owns scheduler registrations for its domain.

## External Dependencies

- **Telegram (via Telethon)** — account creation, warming, posting, commenting. Rate-limited; all calls funnel through `core/telegram_client.py`.
- **SQLite (via SQLAlchemy)** — local file DB sized for ~50 accounts. Single-writer assumption — APScheduler jobs and NiceGUI handlers share one engine through `core/db.py`.
- **Gemini API (via httpx)** — AI comment generation. All calls funnel through a `core/` helper (TBD) so features cannot bypass rate-limiting or key handling.

## What Does NOT Exist Here

- No separate frontend service — NiceGUI is the UI and the server, one process.
- No remote database, queue, or broker — SQLite + APScheduler in-process only.
- No cross-feature imports — shared logic belongs in `services/`, `core/`, or `schemas/`.
- No business logic in `features/` — UI handlers delegate to `services/`.
- No raw `dict`/`tuple`/`list` passing between layers.
- No third-party SDKs (Telethon, SQLAlchemy, loguru, structlog, sentry_sdk) outside their dedicated `core/` wrapper.
