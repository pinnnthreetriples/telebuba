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
last_updated: 2026-06-10
---

# Decisions

## Decision Log

### Introduce `services/` layer between `features/` and `core/`
**Date:** 2026-06-10
**Status:** Active
**Decision:** All business logic moves to `services/<domain>.py`. `features/` becomes UI-thin (NiceGUI page + 3-line handlers that call services). `core/` stays infrastructure-only.
**Reasoning:** Without `services/`, business logic ended up in `features/` and was duplicated across UI handlers and APScheduler jobs. Cross-feature calls would require breaking Rule 1 (no cross-feature imports). With `services/`, the same code path is reused by both UI and scheduler with no duplication. Also makes domain logic UI-agnostic (NiceGUI swappable later).
**Alternatives considered:**
- *Logic in `features/`* (rejected — duplication and Rule 1 violations as soon as two features share logic).
- *Logic in `core/`* (rejected — core is for infrastructure adapters, not domain rules; mixing the two makes both harder to test).
- *Single `services.py` module* (rejected — same god-module problem we banned for features; one file per domain).
**Consequences:** `services/` is the new home of every algorithm. Tests target services directly with mocked `core/*` adapters. Rule 11 added. Layer matrix in architecture.md grows from 3 to 4 layers.

### Typed Telegram actions + central executor
**Date:** 2026-06-10
**Status:** Active
**Decision:** Every Telegram action is a Pydantic class in `schemas/telegram_actions.py` (`JoinChannel`, `PostComment`, `UpdateProfile`, ...). Services and features call `core.telegram_client.execute(account_id, action)`; the executor pattern-matches on `action_type` and dispatches to the right Telethon method.
**Reasoning:** Direct `client.send_message(...)` calls scatter Telethon knowledge across services, make mocking painful, and bypass the central point for rate limits / FloodWait / proxy / outbox. Typed actions give validation at the boundary, audit-friendly logs, and testability without touching Telethon.
**Alternatives considered:**
- *Direct Telethon calls from services* (rejected — see above).
- *String-based action registry* (rejected — loses type safety, defeats the purpose).
**Consequences:** New `schemas/telegram_actions.py`. `core/telegram_client.py` exposes one entry point. Services compose actions; they do not orchestrate Telethon.

### Outbox pattern for non-trivial Telegram actions
**Date:** 2026-06-10
**Status:** ~~Active~~ **Superseded 2026-06-14** (never built) → *direct executor with per-cycle persisted state*.
**Original decision:** Services persist Telegram **intents** (rows in a `telegram_outbox` SQLite table) via `core/db.py`. A worker (APScheduler job or `services/telegram_outbox.py`) picks them up and calls `execute`. Survives crashes; idempotent via `dedupe_key`. Trivial reads bypass the outbox.
**Original reasoning:** Accounts cost money — a crash mid-warming must not lose state or double-act. Synchronous calls offer no recovery story. An outbox gives at-least-once semantics with explicit dedupe.

**Why superseded:** The outbox was never implemented, and the design that actually shipped makes it unnecessary at current scale. Warming runs single-process (one `asyncio.Task` per account in `services/warming.py`); each cycle persists its outcome to `warming_account_state`, and `reconcile_warming_runtime()` rebuilds the loop set on restart. That already gives crash-durable *progress* — a crash loses at most one in-flight action, never accumulated state. A full intent table + worker + dedupe machinery is premature infrastructure (a broker-shaped thing we explicitly rejected) for ~50 accounts in one process.

**Replacement decision (2026-06-14):** Services call `core.telegram_client.execute(account_id, action)` directly; durability is per-cycle state on `warming_account_state`, not a queue. The executor remains the single choke-point for rate limits / FloodWait / proxy. **Reopen** an outbox/queue only when execution goes multi-process.
**Consequences:** No `telegram_outbox` table, no `services/telegram_outbox.py`. The repository-split aggregate list drops `telegram_outbox`. `context/telegram.md` "Crash safety" section documents the replacement.

### Config namespaces — nested Pydantic settings instead of one flat blob
**Date:** 2026-06-10
**Status:** Active
**Decision:** `core/config.py` exposes a `Settings` with **nested namespaces**: `settings.warming`, `settings.gemini`, `settings.telegram`, `settings.sentry`, etc. Each namespace is its own Pydantic model owned by one domain.
**Reasoning:** A single flat `Settings` becomes a 200-field god-object as features grow, and every service ends up importing everything. Namespaces let each service take only the slice it needs (`settings.warming`) and keep config evolution per-domain.
**Alternatives considered:**
- *Flat `Settings`* (rejected — bloat, leaks, harder to test).
- *Per-module config files* (rejected — fragments `.env` loading; pydantic-settings handles nested cleanly).
**Consequences:** Type signature changes — services accept `WarmingSettings` not full `Settings`. `.env` keys use double-underscore convention (`WARMING__MAX_PER_DAY=50`).

### Split `core/db.py` into repositories once tables ≥ 5
**Date:** 2026-06-10
**Status:** Active (deferred trigger)
**Decision:** While the schema has < 5 tables, all DB helpers live in `core/db.py`. Once we cross 5 tables, split into `core/repositories/<aggregate>.py` (one repository per aggregate root: `accounts`, `warming_runs`, `logs`, `telegram_outbox`, ...).
**Reasoning:** A monolithic `core/db.py` is fine at small scale and avoids premature partitioning. As tables proliferate, it becomes a god-module mirroring the feature-isolation problem we already banned. The repository pattern keeps each aggregate's queries colocated.
**Alternatives considered:**
- *Always one file* (rejected — known to break down at scale).
- *Repositories from day one* (rejected — premature; defer until concrete pain).
**Consequences:** Trigger: 5 tables. When triggered, `core/db.py` becomes a barrel re-export for backwards compatibility (one cycle) then is deleted. Until trigger, no action.

### Astral toolchain (uv + ruff + ty) instead of legacy Python tooling
**Date:** 2026-06-10
**Status:** Active
**Decision:** uv for packaging/venv, ruff for lint+format, ty for type-checking. No pip, venv, black, isort, flake8, pyupgrade, pydocstyle, or mypy.
**Reasoning:** One vendor (Astral), Rust-fast, ruff alone replaces 5 tools, ty replaces mypy at 100× speed. Fewer config files, fewer toolchain disagreements.
**Alternatives considered:** Poetry + black + mypy (rejected — slower, more configs, weaker incremental story); pip + venv + flake8 (rejected — same reason, plus dependency resolution headaches).
**Consequences:** No `requirements.txt` — `pyproject.toml` + `uv.lock`. CI must install uv first. `ty` is pre-1.0; if it blocks something, fall back to ruff's type-aware rules or add mypy then.

### Three-tier logging (loguru file + SQLite logs table + NiceGUI Logs page)
**Date:** 2026-06-10
**Status:** Active
**Decision:** Three sinks: loguru rotating `debug.log` for diagnostic noise; SQLite `logs` table for structured business events; NiceGUI page polling that table every 3s with account/status filters. Sentry only for ERROR + unhandled exceptions in production. All access through `core/logging.py`.
**Reasoning:** Operators need a UI filtered by account; that has to come from a queryable store, not a text file. Devs need stacktraces; that wants a file with rotation. Prod needs paging; that's Sentry. One sink couldn't serve all three.
**Alternatives considered:** Only loguru file (rejected — operators can't filter by account in the UI); only SQLite (rejected — stacktraces and debug noise bloat the table and aren't useful in the Logs page); external log aggregator like Loki (rejected — overkill for ~50 accounts in one process).
**Consequences:** `core/logging.py` is the single gateway. Bunch operations must aggregate before logging or the table balloons. Polling-not-push because polling is trivial and 3s latency is fine.

### `schemas/` is shared types, not a layer — `core/` may import it
**Date:** 2026-06-10
**Status:** Active
**Decision:** `schemas/*.py` contains only Pydantic models with dependencies on `pydantic` and `typing`. Both `features/*` and `core/*` may import from `schemas/`. The ORM → Pydantic mapping lives inside `core/db.py`; features receive ready-made schema objects.
**Reasoning:** Rule 2 (Pydantic Boundaries) requires public functions to return Pydantic models. If `core/` cannot import `schemas/`, then `core/db.py` cannot satisfy that — it would have to return ORM objects or dicts, forcing every feature to write the same mapper. Treating `schemas/` as shared types (not a downstream layer) removes the contradiction without weakening any other rule.
**Alternatives considered:**
- *Mapper in `features/`* (rejected — duplication across features, easy to drift; AI agents would write inconsistent mappers).
- *`core/` returns raw dicts* (rejected — violates "no raw dict between layers" in rule 2).
- *Invert dependency: `schemas/` depends on `core/db.py`* (rejected — types should not depend on infrastructure; breaks the mental model and creates import cycles).
**Consequences:** `schemas/` must stay strictly pure — no `core/`, no `features/`, no I/O libraries. If a schema ever needs to import from `core/`, that is a smell — the logic belongs in `core/`, not in the type.

### Use NiceGUI instead of a split frontend/backend
**Date:** 2026-06-10
**Status:** Active
**Decision:** UI and server run as a single async Python process via NiceGUI.
**Reasoning:** No need for a separate JS frontend, no API contract to maintain, one event loop shares the Telethon clients and APScheduler jobs directly.
**Alternatives considered:** FastAPI + React (rejected — two deployables, extra surface for ~50 accounts), Streamlit (rejected — weaker for long-running tasks and bidirectional UI).
**Consequences:** All UI handlers run in-process; long-running work must be scheduled via APScheduler or async tasks so the UI loop stays responsive.

### Use SQLite (via SQLAlchemy) as the only data store
**Date:** 2026-06-10
**Status:** Active
**Decision:** Single SQLite file for accounts, events, and scheduler state.
**Reasoning:** ~50 accounts fits comfortably; one file to back up; trivial local dev; SQLAlchemy keeps the door open to Postgres later.
**Alternatives considered:** Postgres (rejected — operational overhead unjustified at this scale), raw sqlite3 (rejected — loses portability to a bigger DB).
**Consequences:** Single-writer assumption — APScheduler jobs and NiceGUI handlers share one engine. If concurrency becomes a problem, migrate before sharding.

### Feature-per-file with no cross-feature imports
**Date:** 2026-06-10
**Status:** Active
**Decision:** Each feature lives in its own `features/<name>.py`. Existing feature files are never modified to add new features. `features/*.py` cannot import from each other.
**Reasoning:** Keeps features isolated, makes "add a feature" a pure additive change, prevents the slow drift of a god-module.
**Alternatives considered:** Layered package per feature (rejected — overkill at this size), shared `features/common.py` (rejected — that role belongs to `core/`).
**Consequences:** Anything two features both need must be promoted to `core/` or `schemas/`. New features always add a file, never edit one.

### Pydantic schemas as the only inter-layer carrier
**Date:** 2026-06-10
**Status:** Active
**Decision:** All function inputs/outputs that cross a layer boundary are Pydantic models in `schemas/`. No raw dicts.
**Reasoning:** Validation at every edge, types for free, prevents drift between UI / core / DB representations.
**Alternatives considered:** dataclasses (rejected — no validation), TypedDict (rejected — no runtime guarantees), raw dicts (rejected — root cause of half the bugs in projects like this).
**Consequences:** Adding a feature usually means adding a schema first. Helpers in `core/` should take and return models, not dicts.
