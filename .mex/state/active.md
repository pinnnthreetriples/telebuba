---
name: active-state
description: Live project state — what works, what is not yet built, known issues. Updated by the agent in the Record step of GROW after meaningful work.
last_updated: 2026-06-14
---

# Active State

This file is the only place that should change after every task. `ROUTER.md` stays stable.

## Working
- `pyproject.toml` + `uv.lock` resolved; `.venv` on Python 3.13.13.
- Full stack installed and import-verified: nicegui, sqlalchemy, telethon, python-socks, httpx, python-dotenv, pydantic, pydantic-settings, loguru, sentry-sdk, anyio. (apscheduler/structlog removed — unused; warming uses raw asyncio.Task, logging uses loguru.)
- Dev toolchain installed and CLI-verified: ruff, ty, pytest+asyncio+cov, hypothesis, bandit, pip-audit, semgrep, deptry, vulture, radon, pre-commit, respx, factory-boy, aislop.
- `.gitignore` covers `.env`, `*.session`, `*.db`, `*.log`, tool caches.
- `pyproject.toml` configured for maximum test strictness: `filterwarnings=["error"]`, `--strict-markers`, `--strict-config`, `xfail_strict`, `asyncio_mode=strict`, branch coverage with `--cov-fail-under=90`.
- `conftest.py` registers Hypothesis profiles (`strict` = 200 examples, default; `dev` = 50 examples, fast loop).
- `ruff` configured with `select = ["ALL"]` + minimal ignores; `ty` strict imports/references; `bandit`, `deptry`, `vulture` wired in pyproject.
- `.pre-commit-config.yaml` installed and verified end-to-end (13 hooks: generic hygiene + gitleaks + ruff + ruff-format + bandit + ty). `pre-commit install` ran — git hook is wired in `.git/hooks/pre-commit`.
- `.claude/skills/` populated with 10 matt-pocock skills (tdd, diagnose, prototype, grill-with-docs, zoom-out, improve-codebase-architecture, to-prd, to-issues, setup-matt-pocock-skills, git-guardrails-claude-code). Triggers documented in `.mex/AGENTS.md` → Agent Skills.
- `main.py` is the NiceGUI entrypoint; it registers accounts/warming/logs pages and runs on `UI__PORT` (default `8080`, validated via `pydantic-settings`) with reload disabled for predictable local runs. Startup hook calls `services.warming.reconcile_warming_runtime()` to re-attach per-account loops after a restart; shutdown hook cancels them gracefully.
- `core/config.py` uses **`pydantic-settings`** (nested `BaseSettings`, one namespace per domain). All knobs read from typed env: `TELEGRAM__*`, `UI__*`, `DB__*`, `PROXY__*`, `PROFILE_MEDIA__*`, `LOGGING__*`, `WARMING__*`, `GEMINI__*`. A misconfigured `.env` raises a clear `ValidationError` at import time.
- `core/db.py` lazily initializes SQLite via SQLAlchemy and persists accounts in `accounts` plus immutable per-account device fingerprints in `device_fingerprints`. **`PRAGMA foreign_keys=ON`** is now wired on every new connection via SQLAlchemy `event.listens_for(engine, "connect")`, so orphan rows are rejected. Additive migration adds `last_error/last_action/last_channel/heartbeat_at/started_at/stopped_at/flood_wait_seconds/flood_wait_until` to existing `warming_account_state` tables.
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
- **Warming module (full vertical slice).**
  - `schemas/warming.py` — kanban/board/cycle/settings models + `WarmingState`
    lifecycle (`idle`/`active`/`sleeping`/`flood_wait`/`error`) and `warming_health`
    traffic-light mapping. `schemas/gemini.py` — Gemini request/result.
  - `schemas/telegram_actions.py` — extended with `SetOnline`, `ReadChannel`,
    `ReactToPost`, `SendDirectMessage`; `core.telegram_client._dispatch_action`
    handles all four (online toggle, read-history ack, random-post reaction, DM).
  - `core/gemini.py` — the only httpx-to-Google gateway; returns typed `GeminiResult`,
    never raises.
  - `core/db.py` — new tables `warming_channels`, `warming_settings` (singleton row,
    holds Gemini key), `warming_account_state`, with async helpers.
  - `services/warming/` (package) — the engine: channel parsing (unlimited links), per-account
    `run_one_cycle` (online → join → read → maybe react → optional Gemini inter-account
    DM → offline) with humanized pauses, FloodWait short-circuit, and a per-account
    `asyncio` loop task (`_RUNTIME`) sleeping 12-30h between cycles. (See "Structural
    refactors" for the submodule layout.)
  - `features/warming.py` — `/warming` page: drag-and-drop kanban (Idle ↔ Warming
    starts/stops the loop), unlimited channel manager, Gemini key + chat/reactions
    toggles, and a live colour-coded (green/amber/red) activity log with CSS pulse/fade
    animations. `main.py` registers it; Accounts/Logs headers link to it.
- `core/config.py` already uses **nested namespaces**; now also exposes
  `settings.warming` (delays/limits/default reactions) and `settings.gemini`.
- `core/logging.py` (loguru + Sentry + SQLite `logs`) and the Logs page are live. `log_event` is **best-effort**: a failure writing to SQLite `logs` or sending to Sentry is logged via loguru and swallowed, so business operations cannot be broken by a logging fault.
- **Service-layer `.session` upload guardrails.** `services/accounts.import_account_session` rejects empty/oversize uploads (configurable via `PROFILE_MEDIA__SESSION_MAX_BYTES`) and `_write_session_file` best-effort-chmods to `0600` on POSIX. UI no longer the only line of defence.
- **Scaffold-drift cleanup (2026-06-14 audit follow-up).**
  - `tests/test_architecture.py` enforces the layer matrix as tests: features/services/schemas/core import boundaries (no cross-layer or SDK leaks) **and** that `.env.example` covers every `core/config.py` field.
  - `.env.example` regenerated to full coverage — previously missing the entire `TRUST__*` namespace, ~30 `WARMING__*` keys (ramp/quarantine/content/dialogue/active-hours/log-normal/typing-sim), `TELEGRAM__FLOOD_SLEEP_THRESHOLD`, `PROXY__DATACENTER_ASN_KEYWORDS`.
  - Stale docs fixed: removed the `[VERIFY AFTER FIRST IMPLEMENTATION]` marker and documented the real `execute(account_id, action) -> ActionResult` signature in `context/telegram.md`; fixed the self-contradicting "Correct" Telegram example in `context/conventions.md` (it showed direct `get_client().send_message()`, which the same file forbids).
  - **Outbox dropped** (decision B): `telegram_outbox` was never built and is superseded by *direct executor + per-cycle persisted `warming_account_state`*. Reopen only on multi-process execution. (`context/decisions.md`, `context/telegram.md`)
  - **Board N+1 removed**: `load_board()` bulk-loads spam + fingerprints once via new `list_spam_statuses()` / `list_device_fingerprints()` repositories; `_enrich_card` is now pure and scores via `services.trust.account_trust_score_from(...)` (no per-card account/state/spam/fingerprint re-fetch). Board DB cost is now constant, not O(accounts).
- **Structural refactors (2026-06-14 audit follow-up #8 / #11).**
  - **`core/db.py` repository split complete.** `core/db.py` is now *shared plumbing only* — `MetaData`, all table definitions, engine lifecycle, the additive-migration hook, and the generic row/value helpers (`_now_iso`, `_optional_str/_int`, `_required_int`, `_row_to_device_fingerprint`). Every aggregate's queries live in `core/repositories/`: `accounts` (accounts + `account_proxies`), `logs`, `warming` (channels + settings + account state), plus the pre-existing `content` / `device_fingerprint` / `dialogues` / `spam_status`. `core/db.py` re-exports their public functions at the bottom so `from core.db import ...` is unchanged. Tables and `_get_engine` stay in `core/db.py` (repos import them).
  - **`services/warming.py` → `services/warming/` package.** Cohesive submodules extracted: `channels.py` (input parsing/list/add/remove), `settings_store.py` (settings row; named `_store` to avoid shadowing `core.config.settings` in the package namespace), `board.py` (kanban read model), `pacing.py` (pure scheduling/intensity/timezone helpers). The **engine stays in `__init__.py`** — state transitions, cycle, Gemini chat, quarantine recovery, runtime loop — because tests `monkeypatch.setattr(warming, "execute"/"generate_text"/"refresh_spam_status"/"_warming_loop"/"_rng", ...)` on the package namespace, so the seam imports and their callers must share that namespace. Public API unchanged (re-exported via `__all__`); all 80 warming tests pass untouched.
- **`services/warming` hardening** (critical-analysis sweep): explicit `try/finally` around `SetOnline(True/False)` so an account never stays online forever; `failed` action-statuses are surfaced (cycle returns `status="failed"` if every action failed); `FloodWait` propagates `flood_wait_seconds`/`flood_wait_until` through `WarmingCycleResult` and into `warming_account_state`; per-account `asyncio.Lock` on start/stop; `start_warming` raises `UnknownAccountError` for missing accounts; `stop_warming` `await`s task cancel with timeout; `reconcile_warming_runtime` rebuilds `_RUNTIME` on startup; channel input is regex-validated and bounded by `WARMING__MAX_CHANNELS_TOTAL/PER_ADD/LENGTH`; Gemini DM text is sanitised (strip control chars, cap chars+lines) before send; `gemini_model` editable from UI; UI gained explicit Clear-key checkbox and warming-only activity filter.

## Not Yet Built
- Additional `features/` pages — comments page.
- `services/comments.py` — not started. (`services/telegram_outbox.py` is no longer
  planned — outbox dropped, see "Outbox dropped" above.)
- APScheduler / `core/scheduler.py` — deliberately **not** used for warming (see note
  below); still needed if a future feature wants true cron scheduling.
- Comment-generation use of Gemini (only inter-account chat uses it today).

## Known Issues
- `aislop --version` fails on Windows due to a space in the Python path — call via `uv run python -m aislop` instead.

## Open Decisions

Authoritative list of architectural unknowns. Context files may carry `[TO BE DETERMINED]` markers; this section is the single index of all of them.

### Architecture / design (must be resolved before related code is written)
- **Account lifecycle enum beyond session health** — session health is stored on `accounts.status`; warming lifecycle now lives separately in `warming_account_state.state` (`idle`/`active`/`sleeping`/`flood_wait`/`error`). A unified business lifecycle (`created` / `verified` / `active` / `banned`) is still undecided. (`context/telegram.md`)
- **RESOLVED — warming runtime model.** Warming is a continuous randomised loop, so each account runs an `asyncio.Task` owned by `services/warming.py` (`_RUNTIME`), not an APScheduler job. A shared `core/scheduler.py` is only needed if a future feature wants real cron scheduling. (`context/warming.md`)
- **RESOLVED — human-like activity tuning.** Per-action 10-30s jitter, 5-30s "typing", 8-45s "reading", random 1-3 channels/cycle, configurable reaction probability, and a 12-30h post-cycle sleep — all in `settings.warming`. Per-account daily quotas remain a future refinement. (`context/warming.md`)
- **`log_event` signature** — exact kwargs of the `core/logging.py` helper. Locked in when `core/logging.py` ships. (`context/logging.md`)
- **NiceGUI Logs page pagination** — limit + offset strategy on the SQLite `logs` query. (`context/logging.md`)
- **RESOLVED — `core/telegram_client.execute` signature.** `async def execute(account_id: str, action: TelegramAction) -> ActionResult`; `ActionResult` carries `status`/`action_type`/`account_id` + optional `message_id`/`flood_wait_seconds`/`error_type`/`error_message`. (`context/telegram.md`)
- **RESOLVED — `telegram_outbox`.** Dropped (decision B); replaced by direct executor + per-cycle persisted state. No table, no worker. (`context/decisions.md`)
- **RESOLVED — `core/db.py` repositories split.** Done (2026-06-14): `core/db.py` is shared plumbing; every aggregate lives in `core/repositories/`. (`context/architecture.md`)

### Tooling / process
- **Project purpose / "why"** — deliberately deferred; not documented anywhere.
- **Mutation testing (`mutmut`)** — consider adding once `core/warming.py` and `core/telegram_client.py` stabilize. Needs real source + tests to produce signal. When ready: `uv add --dev mutmut`, target critical modules (`--paths-to-mutate=core/warming.py`), run nightly or via `workflow_dispatch`, never gate PR merges on it.
