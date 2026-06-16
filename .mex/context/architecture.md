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
    condition: when working with runtime workflow tasks
  - target: context/logging.md
    condition: when working with the three-tier logging architecture
last_updated: 2026-06-16
---

# Architecture

## System Overview

User opens the NiceGUI web interface → interacts with a thin page/component in `features/` → inputs are validated through Pydantic models from `schemas/` → the handler delegates to a `services/` domain package/module → the service uses `core/` gateways for I/O: SQLite through `core/db.py` + `core/repositories/*`, Telegram through `core/telegram_client/`, Gemini through `core/gemini.py`, logging through `core/logging.py`.

Long-running runtime workflows are owned by service-layer async tasks. The current warming runtime uses per-account `asyncio.Task`s owned by `services/warming/_runtime.py`; APScheduler is not used for that domain.

Single-process async runtime — NiceGUI hosts the event loop, no separate frontend/backend.

## Folder Structure

```text
telebuba/
├── main.py                       NiceGUI composition root: setup, page registration, runtime startup/shutdown hooks
├── pyproject.toml                uv project + strict test/lint/security gates
├── .env                          local secrets (gitignored)
├── .env.example                  committed template; architecture tests keep it synced with core/config.py
├── core/                         shared infrastructure — only layer touching third-party SDKs
│   ├── config.py                 pydantic-settings; nested namespaces
│   ├── db.py                     SQLite metadata, table definitions, engine lifecycle, additive migrations, re-exports
│   ├── repositories/             per-aggregate DB query modules
│   ├── telegram_client/          Telethon gateway package; public API re-exported from core.telegram_client
│   ├── gemini.py                 HTTP gateway for Gemini
│   └── logging.py                loguru + SQLite logs + optional Sentry gateway
├── schemas/                      Pydantic models; shared types, no behavior, no I/O
│   └── <domain>.py               one file per domain contract
├── services/                     business logic — pure, reusable, no UI, no SDK imports
│   ├── accounts/                 account/session/profile/proxy operations
│   └── warming/                  runtime workflow domain package
├── features/                     UI-thin NiceGUI pages/components; delegates logic to services/
│   ├── accounts/
│   ├── warming/
│   └── logs.py
└── tests/                        mirrors source tree; pytest + property tests + architecture tests
```

A small domain may start as a single module (`features/logs.py`, `services/comments.py`). Once it grows, it should become a package with a thin `__init__.py` and cohesive submodules. Package split is now normal, not an exception.

## Layer Model

Four layers, top (UI) to bottom (infra). `schemas/` is the shared types side-band, not a downstream layer.

```text
main.py
  └─ features/*        UI-thin pages/components/click handlers. NO business logic.
       └─ services/*   business logic, state transitions, runtime workflow orchestration.
            └─ core/*  infrastructure gateways: db/repositories, telegram_client, gemini, config, logging.
                 └─ schemas/*    Pydantic models — pure data, no project imports.
```

`schemas/` is shared types, not a downstream layer. All layers may import schema types; schemas must not import layers.

## Allowed Imports

| Layer | May import |
| --- | --- |
| `features/**` | `services/*`, `core/*`, `schemas/*`, NiceGUI. UI logic only — business logic goes to `services/`. |
| `services/**` | other `services/*`, `core/*`, `schemas/*`. Composition between services is allowed. |
| `core/**` | `schemas/*`, stdlib, third-party SDKs. Not `features/*` or `services/*`. |
| `schemas/*.py` | `pydantic`, `typing`, stdlib typing helpers only. |
| `tests/**` | anything (verification layer). |

## Forbidden Imports (each breaks a non-negotiable)

| In… | Must NOT import | Why |
| --- | --- | --- |
| any `features/<a>/**` | `features/<b>/**` (another feature) | Rule 1 — Feature Isolation |
| any `features/**` | `sqlalchemy`, `telethon` | Rule 6 — gateways only via `core/*` |
| any `services/**` | `sqlalchemy`, `telethon`, raw provider SDK/HTTP clients | Rule 6 — services use `core/` adapters, never SDKs directly |
| any `services/**` | `nicegui`, anything UI-specific | Rule 11 — services are UI-agnostic |
| any module outside `core/logging.py` | `loguru`, `sentry_sdk` | Rule 4 — logging only via `core/logging.py` |
| any module outside `core/config.py` | `os.environ`, raw `dotenv` | Rule 3 — config only via `core/config.py` |
| `schemas/*` | `core/*`, `services/*`, `features/*`, any I/O library | Rule 5 — schemas are pure data, no behavior |
| `core/*` | `services/*`, `features/*` | Rule 5 — core does not know about services or features |

Rule numbers reference the canonical list in `context/conventions.md`. `.mex/AGENTS.md` mirrors the same rules in one-line form.

## Data Crossing Layers

Any public function whose caller is in a different layer takes and returns Pydantic models from `schemas/` or `None`.
Raw `dict`, `tuple`, and `list` of raw values are forbidden as cross-layer parameters or return values. If multiple items must cross a boundary, wrap them in a Pydantic response model.
Inside a single function or private module, regular Python data structures are fine.

**Sanity check:** a repository maps SQLAlchemy rows into `schemas.accounts.AccountRead` or another Pydantic model before returning. Features receive ready-made schema objects; they do not map ORM rows.

## Key Components

- **`main.py`** — composition root: setup logging, register pages, run NiceGUI, reconcile runtime tasks on startup, shutdown runtime tasks gracefully.
- **`core/config.py`** — typed settings via `pydantic-settings`. Nested namespaces per domain (`settings.warming`, `settings.gemini`, `settings.telegram`, ...). Single source of truth for tunables and secrets.
- **`core/db.py`** — shared SQLite plumbing only: SQLAlchemy metadata, table definitions, engine lifecycle, additive migration hook, generic row/value helpers, and compatibility re-exports.
- **`core/repositories/*`** — per-aggregate DB query modules: accounts, warming, logs, content, device_fingerprint, dialogues, spam_status.
- **`core/telegram_client/`** — Telethon gateway package. Public imports still come from `core.telegram_client`; implementation is split into focused private modules.
- **`core/gemini.py`** — Gemini HTTP gateway. Only place raw Gemini HTTP calls belong.
- **`core/tdata_import.py`** — opentele2 adapter. Safe-extracts `tdata.zip` and writes Telethon `.session` files.
- **`core/logging.py`** — loguru file sink + SQLite `logs` + optional Sentry. The only logging gateway.
- **`schemas/*.py`** — Pydantic models flowing between UI, services, core, and DB. `schemas/telegram_actions.py` declares Telegram actions as typed Pydantic classes.
- **`services/accounts/`** — account lifecycle/actions, session import/check, proxy operations, profile/media actions; UI-agnostic.
- **`services/warming/`** — runtime workflow package: channel parsing, settings storage, board read model, pacing/readiness, cycle execution, runtime task ownership.
- **`features/accounts/` / `features/warming/` / `features/logs.py`** — UI-thin NiceGUI pages; validate, call services, render.

## External Dependencies

- **Telegram (via Telethon)** — all calls funnel through `core/telegram_client/` and typed actions.
- **SQLite (via SQLAlchemy)** — local file DB for the current single-process deployment. DB access goes through `core/db.py` and `core/repositories/*`.
- **Gemini API (via httpx)** — AI text generation through `core/gemini.py` only.
- **NiceGUI** — UI and HTTP server in one async Python process.

## What Does NOT Exist Here

- No separate frontend service — NiceGUI is the UI and server, one process.
- No remote database, queue, broker, or worker process — SQLite + in-process async runtime.
- No APScheduler for the current warming runtime — add a scheduler only if a future feature needs true cron semantics.
- No `telegram_outbox` — direct executor + persisted per-cycle state is the current crash-safety model.
- No cross-feature imports — shared logic belongs in `services/`, `core/`, or `schemas/`.
- No business logic in `features/` — UI handlers delegate to services.
- No raw `dict`/`tuple`/`list` crossing layer boundaries.
- No third-party SDKs (Telethon, SQLAlchemy, loguru, sentry_sdk, raw provider HTTP clients) outside their dedicated `core/` wrapper.
