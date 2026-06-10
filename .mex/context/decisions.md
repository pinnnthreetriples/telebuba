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
