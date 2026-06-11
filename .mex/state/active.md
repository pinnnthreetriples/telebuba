---
name: active-state
description: Live project state — what works, what is not yet built, known issues. Updated by the agent in the Record step of GROW after meaningful work.
last_updated: 2026-06-11
---

# Active State

This file is the only place that should change after every task. `ROUTER.md` stays stable.

## Working
- `pyproject.toml` + `uv.lock` resolved; `.venv` on Python 3.13.13.
- Full stack installed and import-verified: nicegui, sqlalchemy, telethon, python-socks, apscheduler, httpx, python-dotenv, pydantic, loguru, structlog, sentry-sdk, anyio.
- Dev toolchain installed and CLI-verified: ruff, ty, pytest+asyncio+cov, hypothesis, bandit, pip-audit, semgrep, deptry, vulture, radon, pre-commit, respx, factory-boy, aislop.
- `.gitignore` covers `.env`, `*.session`, `*.db`, `*.log`, tool caches.
- `pyproject.toml` configured for maximum test strictness: `filterwarnings=["error"]`, `--strict-markers`, `--strict-config`, `xfail_strict`, `asyncio_mode=strict`, branch coverage with `--cov-fail-under=90`.
- `conftest.py` registers Hypothesis profiles (`strict` = 200 examples, default; `dev` = 50 examples, fast loop).
- `ruff` configured with `select = ["ALL"]` + minimal ignores; `ty` strict imports/references; `bandit`, `deptry`, `vulture` wired in pyproject.
- `.pre-commit-config.yaml` installed and verified end-to-end (13 hooks: generic hygiene + gitleaks + ruff + ruff-format + bandit + ty). `pre-commit install` ran — git hook is wired in `.git/hooks/pre-commit`.
- `.claude/skills/` populated with 10 matt-pocock skills (tdd, diagnose, prototype, grill-with-docs, zoom-out, improve-codebase-architecture, to-prd, to-issues, setup-matt-pocock-skills, git-guardrails-claude-code). Triggers documented in `.mex/AGENTS.md` → Agent Skills.
- `main.py` is the NiceGUI entrypoint; it registers the accounts page and runs on `TELEBUBA_PORT` / `settings.ui_port` (default `8080`) with reload disabled for predictable local runs.
- `core/config.py` provides typed settings for Telegram API credentials, SQLite DB path, session dir, UI port, and Telethon retry/timeouts.
- `core/db.py` lazily initializes SQLite via SQLAlchemy and persists accounts in `accounts` plus immutable per-account device fingerprints in `device_fingerprints`.
- `core/device_fingerprint.py` generates one random desktop device profile per account and returns the saved profile on later calls.
- `core/telegram_client.py` prepares a Pydantic client profile, then creates Telethon clients with the saved device fingerprint.
- `core/telegram_client.py` checks Telegram sessions via `check_telegram_session()` and returns typed statuses without deleting session files.
- `features/accounts.py` owns the NiceGUI accounts page: metrics, search/status filters, account table, `.session` upload/import dialog, **`tdata.zip` upload/import**, selected/all session-check actions.
- `core/tdata_import.py` converts uploaded `tdata.zip` to Telethon `.session` files via opentele2 with safe-extract guarantees (500 MiB cap, 50k entries, no `..` / absolute / symlinks; temp dir wiped on every path).
- `schemas/tdata.py` defines Pydantic schemas for the conversion request/result and the per-account summary.
- `schemas/accounts.py` defines Pydantic account schemas, table rows, filters, summaries, and session-health statuses (`new`, `alive`, `unauthorized`, `session_error`, `account_error`, `flood_wait`, `network_error`, `proxy_error`, `unknown_error`).
- `schemas/device_fingerprint.py` defines Pydantic schemas for device fingerprints and Telegram client profiles.
- `schemas/telegram_session.py` defines Pydantic schemas for session check requests/results.
- Account proxies are stored per account in SQLite table `account_proxies`; `services.accounts`
  exposes save/delete proxy operations and `core.telegram_client.py` applies saved proxies to
  every Telethon client via python-socks.
- Account profile editing is user-facing from the accounts page and goes through
  `services.accounts.update_account_profile()` -> typed `UpdateProfile` action ->
  `core.telegram_client.execute()`, then persists the local first/last/username/bio snapshot.

## Not Yet Built
- `core/logging.py` (loguru + structlog + Sentry + SQLite `logs` table).
- `core/config.py` — refactor to **nested namespaces** (`settings.telegram`, `settings.warming`, `settings.gemini`, ...). Current shape is flat.
- `services/` — entire layer (warming, accounts, comments, telegram_outbox worker). None exists today; methodology requires all business logic here.
- `schemas/telegram_actions.py` — typed Telegram action classes (`JoinChannel`, `PostComment`, `UpdateProfile`, ...).
- `features/accounts.py` — already implemented, but predates the services/ layer; orchestration lives in the feature file directly. Refactor to extract `services/accounts.py` is tracked separately.
- Additional `features/` pages — warming, comments, logs page.
- Real NiceGUI entrypoint for non-accounts pages in `main.py`.
- SQLAlchemy models beyond `accounts` + `device_fingerprints`: `logs`, `telegram_outbox`, etc.
- APScheduler wiring (either a `core/scheduler.py` or owned by `features/warming.py`).
- `.env.example`.
- AI provider integration — Gemini API planned (`GEMINI_API_KEY` reserved).

## Known Issues
- `aislop --version` fails on Windows due to a space in the Python path — call via `uv run python -m aislop` instead.

## Open Decisions

Authoritative list of architectural unknowns. Context files may carry `[TO BE DETERMINED]` markers; this section is the single index of all of them.

### Architecture / design (must be resolved before related code is written)
- **Account lifecycle enum beyond session health** — session health is stored on `accounts.status`; business lifecycle (`created` / `verified` / `warming` / `active` / `banned`) still needs a canonical list + column decision. (`context/telegram.md`)
- **Shared scheduler handle** — where `AsyncIOScheduler` is exposed so non-warming features can register jobs without `from features.warming`. Probably `core/scheduler.py`. (`context/warming.md`)
- **APScheduler jobstore** — in-memory + DB-tracked, or APScheduler's own SQLAlchemy jobstore pointing at `telebuba.db`. (`context/warming.md`)
- **Human-like activity tuning** — jitter strategy around scheduled times, per-account daily quotas, which actions count as warming vs active. (`context/warming.md`)
- **`log_event` signature** — exact kwargs of the `core/logging.py` helper. Locked in when `core/logging.py` ships. (`context/logging.md`)
- **NiceGUI Logs page pagination** — limit + offset strategy on the SQLite `logs` query. (`context/logging.md`)
- **`core/telegram_client.execute(action)` signature** — exact return shape (`ActionResult` union? per-action result schema?). (`context/telegram.md`)
- **Initial `schemas/telegram_actions.py` action set** — which actions ship in the first cut (likely `JoinChannel`, `PostComment`, `UpdateProfile`, `LeaveChannel`). (`context/telegram.md`)
- **`telegram_outbox` table schema** — columns, indexes, retry/backoff policy, `dedupe_key` generation, worker owner module. (`context/telegram.md`)
- **`core/db.py` → repositories split trigger** — when to break into `core/repositories/<aggregate>.py`. Current rule: ≥ 5 tables. (`context/architecture.md`)

### Tooling / process
- **Project purpose / "why"** — deliberately deferred; not documented anywhere.
- **Mutation testing (`mutmut`)** — consider adding once `core/warming.py` and `core/telegram_client.py` stabilize. Needs real source + tests to produce signal. When ready: `uv add --dev mutmut`, target critical modules (`--paths-to-mutate=core/warming.py`), run nightly or via `workflow_dispatch`, never gate PR merges on it.
