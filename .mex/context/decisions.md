---
name: decisions
description: Key architectural and technical decisions with reasoning. Load when making design choices or understanding why something is built a certain way.
triggers:
  - "why do we"
  - "why is it"
  - "decision"
  - "alternative"
  - "we chose"
edges:
  - target: context/architecture.md
    condition: when a decision relates to system structure
  - target: context/stack.md
    condition: when a decision relates to technology choice
last_updated: 2026-06-16
---

# Decisions

## Decision Log

### Introduce `services/` layer between `features/` and `core/`
**Date:** 2026-06-10
**Status:** Active
**Decision:** All business logic lives in `services/<domain>/` or `services/<domain>.py`. `features/` stays UI-thin (NiceGUI page/components + handlers that call services). `core/` stays infrastructure-only.
**Reasoning:** Without `services/`, business logic ends up in UI handlers and cannot be reused safely from runtime tasks, scripts, or another feature without cross-feature imports. With `services/`, the same code path is reused without UI coupling.
**Alternatives considered:**
- *Logic in `features/`* (rejected — duplication and feature-boundary violations).
- *Logic in `core/`* (rejected — core is for infrastructure adapters, not domain rules).
- *Single `services.py` module* (rejected — god-module risk).
**Consequences:** `services/` is the home of every algorithm/state transition/domain operation. Tests target services directly with mocked `core/*` adapters. Layer matrix in architecture.md has four layers.

### Typed Telegram actions + central executor
**Date:** 2026-06-10
**Status:** Active
**Decision:** Every Telegram action is a Pydantic class in `schemas/telegram_actions.py` (`JoinChannel`, `PostComment`, `UpdateProfile`, ...). Services and features call `core.telegram_client.execute(account_id, action)`; the executor pattern-matches on the action model and dispatches inside the core gateway.
**Reasoning:** Direct SDK calls scattered across services make mocking painful and bypass the central point for lifecycle/error/logging policy. Typed actions give validation at the boundary, audit-friendly logs, and testability without touching Telethon outside `core/`.
**Alternatives considered:**
- *Direct Telethon calls from services* (rejected — bypasses the gateway).
- *String-based action registry* (rejected — loses type safety).
**Consequences:** `schemas/telegram_actions.py` owns action schemas. `core/telegram_client/` exposes one public executor. Services compose actions; they do not orchestrate raw SDK calls.

### Outbox pattern for non-trivial Telegram actions
**Date:** 2026-06-10
**Status:** Superseded 2026-06-14 (never built) → direct executor with per-cycle persisted state.
**Original decision:** Services persist Telegram intents in a `telegram_outbox` SQLite table. A worker picks them up and calls `execute`.
**Why superseded:** The outbox was never implemented, and the shipped design is single-process. Runtime progress is persisted on `warming_account_state`; `reconcile_warming_runtime()` rebuilds in-memory tasks on restart. A separate intent table + worker is premature until execution becomes multi-process.
**Replacement decision:** Services call `core.telegram_client.execute(account_id, action)` directly. Durability is per-cycle state, not a queue. Reopen an outbox/queue only when execution goes multi-process.
**Consequences:** No `telegram_outbox` table and no `services/telegram_outbox.py`.

### Config namespaces — nested Pydantic settings instead of one flat blob
**Date:** 2026-06-10
**Status:** Active
**Decision:** `core/config.py` exposes a `Settings` with nested namespaces: `settings.warming`, `settings.gemini`, `settings.telegram`, `settings.sentry`, etc. Each namespace is its own Pydantic model owned by one domain.
**Reasoning:** A single flat `Settings` becomes a 200-field god-object. Namespaces let each service take the slice it needs and keep config evolution per-domain.
**Alternatives considered:**
- *Flat `Settings`* (rejected — bloat, leaks, harder to test).
- *Per-module config files* (rejected — fragments `.env` loading; pydantic-settings handles nested cleanly).
**Consequences:** `.env` keys use double-underscore convention (`WARMING__MAX_PER_DAY=50`). `.env.example` must mirror `core/config.py`; architecture tests enforce this.

### Split `core/db.py` into repositories once tables ≥ 5
**Date:** 2026-06-10
**Status:** Implemented 2026-06-14
**Decision:** Per-aggregate CRUD lives in `core/repositories/<aggregate>.py`. `core/db.py` remains shared SQLite plumbing: metadata, table definitions, engine lifecycle, additive migrations, generic helpers, and compatibility re-exports.
**Reasoning:** A monolithic `core/db.py` was turning into a god-module. The repository pattern keeps aggregate queries colocated while preserving a single SQLAlchemy gateway.
**Alternatives considered:**
- *Always one file* (rejected — breaks down as tables grow).
- *Repositories from day one* (initially deferred; implemented after the trigger was reached).
**Consequences:** Current repositories include accounts, warming, logs, content, device_fingerprint, dialogues, and spam_status. Existing `from core.db import ...` call sites still work via re-exports, but new aggregate queries should live in `core/repositories/*`.

### Astral toolchain (uv + ruff + ty) instead of legacy Python tooling
**Date:** 2026-06-10
**Status:** Active
**Decision:** uv for packaging/venv, ruff for lint+format, ty for type-checking. No pip, venv, black, isort, flake8, pyupgrade, pydocstyle, or mypy.
**Reasoning:** One vendor, Rust-fast, fewer configs, fewer toolchain disagreements.
**Alternatives considered:** Poetry + black + mypy (rejected — slower, more configs); pip + venv + flake8 (rejected — same reason, plus weaker dependency resolution).
**Consequences:** No `requirements.txt` — `pyproject.toml` + `uv.lock`. `ty` is pre-1.0; revisit if it blocks work.

### Strict quality gates
**Date:** 2026-06-16
**Status:** Active
**Decision:** CI and pre-commit enforce ruff, ruff-format, ty, bandit, deptry, vulture, radon, pip-audit, semgrep, and aislop. Aislop is zero-tolerance in CI and pre-push.
**Reasoning:** The project is agent-edited, so structural drift, dead code, weak typing, and generated-code artifacts must fail before merge.
**Alternatives considered:** Manual review only (rejected — too easy to miss drift); relaxed linting (rejected — agentic edits need a hard floor).
**Consequences:** Package splits were driven by size/complexity gates. New work must keep the toolchain green.

### Three-tier logging (loguru file + SQLite logs table + NiceGUI Logs page)
**Date:** 2026-06-10
**Status:** Active
**Decision:** Three sinks: loguru rotating `debug.log` for diagnostic noise; SQLite `logs` table for structured business events; NiceGUI Logs page polling that table. Sentry only for ERROR + unhandled exceptions in production. All access through `core/logging.py`.
**Reasoning:** Operators need a UI filtered by account/status; devs need stacktraces; prod needs alerting. One sink cannot serve all three.
**Alternatives considered:** Only loguru file (rejected — no UI filtering); only SQLite (rejected — stacktraces/debug noise bloat the table); external log aggregator (rejected — overkill for current single-process scope).
**Consequences:** `core.logging.log_event()` is the single business-event gateway. Logging failures are best-effort and must not break business operations.

### `schemas/` is shared types, not a layer — `core/` may import it
**Date:** 2026-06-10
**Status:** Active
**Decision:** `schemas/*.py` contains only Pydantic models with dependencies on Pydantic and typing/stdlib helpers. Both `features/*` and `core/*` may import from `schemas/`.
**Reasoning:** Public gateways must return Pydantic models. If `core/` cannot import `schemas/`, it would return ORM objects or dicts and force every caller to write mappers.
**Alternatives considered:**
- *Mapper in `features/`* (rejected — duplication and drift).
- *`core/` returns raw dicts* (rejected — violates Pydantic-boundary rule).
- *Invert dependency: `schemas/` depends on `core/db.py`* (rejected — import cycles and impure schemas).
**Consequences:** `schemas/` must stay strictly pure — no `core/`, no `features/`, no I/O libraries.

### Use NiceGUI instead of a split frontend/backend
**Date:** 2026-06-10
**Status:** Active
**Decision:** UI and server run as a single async Python process via NiceGUI.
**Reasoning:** No separate JS frontend, no API contract to maintain, one event loop shares app runtime state.
**Alternatives considered:** FastAPI + React (rejected — two deployables), Streamlit (rejected — weaker for long-running interactive workflows).
**Consequences:** Long-running work must be async and must not block the UI loop.

### Use SQLite (via SQLAlchemy) as the only data store
**Date:** 2026-06-10
**Status:** Active
**Decision:** Single SQLite file for accounts, events, runtime state, and settings.
**Reasoning:** Current scale fits comfortably; one file to back up; trivial local dev; SQLAlchemy keeps the door open to Postgres later.
**Alternatives considered:** Postgres (rejected — operational overhead unjustified at this scale), raw sqlite3 (rejected — loses portability to a bigger DB).
**Consequences:** Single-process assumption. If concurrency becomes a problem, migrate before sharding.

### Feature-per-module/package with no cross-feature imports
**Date:** 2026-06-10
**Status:** Updated 2026-06-16
**Decision:** Each feature owns one module or package under `features/`. Small features can be a single file; larger features become a package with a thin `__init__.py` and focused submodules. Feature domains cannot import each other.
**Reasoning:** The original one-file rule prevented premature structure, but `features/accounts.py` and `features/warming.py` grew past healthy limits. Package-per-feature keeps isolation while avoiding god-files.
**Alternatives considered:** Force one file forever (rejected — file-size/complexity gates fail); shared `features/common.py` (rejected — shared logic belongs in services/core/schemas).
**Consequences:** New behavior may extend an existing feature package only when it belongs to that same feature domain. Cross-domain shared logic still moves to `services/`, `core/`, or `schemas/`.

### Pydantic schemas as the only inter-layer carrier
**Date:** 2026-06-10
**Status:** Active
**Decision:** All function inputs/outputs that cross a layer boundary are Pydantic models in `schemas/`. No raw dicts or raw list/tuple payloads.
**Reasoning:** Validation at every edge, types for free, prevents drift between UI / core / DB representations.
**Alternatives considered:** dataclasses (rejected — weaker validation), TypedDict (rejected — no runtime guarantees), raw dicts (rejected — root cause of drift bugs).
**Consequences:** Adding a feature usually means adding or extending a schema first. Multi-item returns should use wrapper response models.
