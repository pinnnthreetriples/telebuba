---
name: active-state
description: Live project state — what works, what is not yet built, known issues. Updated by the agent in the Record step of GROW after meaningful work.
last_updated: 2026-06-20
---

# Active State

This file is the only place that should change after every meaningful task. `ROUTER.md` stays stable.

## Working

- `pyproject.toml` + `uv.lock` resolved; local target is Python 3.13.x.
- Runtime stack is installed and import-verified: NiceGUI, SQLAlchemy/SQLite, Telethon, python-socks, httpx, python-dotenv, pydantic, pydantic-settings, loguru, sentry-sdk, anyio. APScheduler and structlog are intentionally unused/removed.
- Dev toolchain is wired: ruff, ty, pytest+asyncio+cov, hypothesis, bandit, pip-audit, semgrep, deptry, vulture, radon, pre-commit, respx, factory-boy, aislop.
- Strict quality gates are active: warnings-as-errors, pytest strict config, branch coverage floor 90%, ruff `ALL`, ty strict unresolved-reference rules, bandit, deptry, vulture, radon, semgrep, pip-audit, and zero-tolerance aislop. Suite currently at 575 tests green, coverage ≥ 90%.
- `.pre-commit-config.yaml` and CI run the same major gates. Aislop runs at pre-push and in CI with Node available.
- `.gitignore` covers `.env`, `*.session`, `*.db`, `*.log`, and tool caches.
- `main.py` is the NiceGUI composition root. It registers accounts/warming/logs pages, runs on `UI__PORT`, calls `services.warming.reconcile_warming_runtime()` on startup, and shuts runtime tasks down gracefully.
- `core/config.py` uses nested `pydantic-settings` namespaces: `TELEGRAM__*`, `UI__*`, `DB__*`, `PROXY__*`, `PROFILE_MEDIA__*`, `LOGGING__*`, `WARMING__*`, `GEMINI__*`, `TRUST__*`.
- `.env.example` has full config coverage and is checked by `tests/test_architecture.py`.
- `core/db.py` is now shared SQLite plumbing only: metadata, table definitions, engine lifecycle, generic row helpers, and compatibility re-exports. Schema evolution lives in `core/migrations.py` as a versioned, append-only registry stamped in a `schema_version` table — `apply_migrations()` runs every unstamped migration idempotently, so legacy databases that already carry the columns are stamped without errors.
- Per-aggregate DB queries live in `core/repositories/`: accounts, warming, logs, content, device_fingerprint, dialogues, spam_status.
- SQLite foreign keys are enabled on every new connection via `PRAGMA foreign_keys=ON`.
- `core/telegram_client/` is a gateway package. Public API is re-exported from `core.telegram_client`; implementation is split into focused private modules.
- `core/gemini.py` is the HTTP gateway for Gemini and returns typed results.
- `core/logging.py` is live: loguru diagnostic file + SQLite `logs` table + optional Sentry. `log_event()` is best-effort and does not break business operations when a sink fails.
- `core/tdata_import.py` converts uploaded `tdata.zip` to Telethon `.session` files with safe-extract guardrails.
- `services/accounts/` is a thin re-export package. Implementations live in per-concern submodules: `lifecycle.py` (registration + geo), `sessions.py` (`.session` and tdata import + liveness check, with the `_tdata`/`_uploads`/`_table` helpers), `proxy.py`, `profile.py`, `media.py`. Tests monkeypatch external collaborators on the owning submodule (`services.accounts.sessions.convert_tdata_zip`, `services.accounts.proxy.check_proxy_connectivity`, `services.accounts.profile.execute`, `services.accounts.media.execute`).
- `import_account_tdata` returns a `TdataImportResult` Pydantic wrapper rather than a raw `list[AccountRead]`, keeping the service boundary Pydantic-only and leaving room for per-import metadata. Same treatment applied to `services.dialogues.get_partners` → `DialoguePartnersResult` and `services.dialogues.assign_pairs` → `DialoguePairsResult`.
- `tests/test_architecture.py` walks each layer with `rglob("*.py")` (no auto-skip for `__init__.py`), adds a cross-feature isolation test, and a regression test that the package submodules (`services/accounts/`, `services/warming/`, `features/accounts/`, `features/warming/`, `core/telegram_client/`, `core/repositories/`) are actually reached by the scan.
- `features/accounts/` is a NiceGUI page package, split into focused rendering modules: `_header.py` (toolbar + nav), `_metrics.py` (metric tiles), `_table_section.py` (search + table), `_controller.py` (event handlers / page state), `_page.py` (route + wiring), `_dialogs.py`, `_table.py`. `__init__.py` is re-export only — `register_accounts_page` is the public entry. The controller catches `ValueError` from `check_account_session` and surfaces it via `ui.notify` instead of letting it escape into NiceGUI's logs.
- `services/warming/` is a package. Submodules: `channels.py`, `settings_store.py`, `board.py`, `pacing.py`, `_seams.py`, `_state.py`, `_chat.py`, `_cycle.py`, `_transitions.py`, `_loop.py`, `_runner.py`, `_purge.py`, `_runtime.py`.
- `features/warming/` is a UI-only package for config cards, channel UI, board rendering, and activity/log UI.
- Warming runtime model is resolved: per-account `asyncio.Task`s owned by `services/warming/_runtime.py`, not APScheduler.
- `telegram_outbox` is resolved/dropped. Current model is direct typed executor + persisted per-cycle runtime state. Reopen queue/outbox only for multi-process execution.
- Board N+1 was removed. `load_board()` bulk-loads required signals once and keeps card enrichment pure.
- Scaffold memory refresh completed on 2026-06-16: `.mex/AGENTS.md`, architecture/conventions/decisions/stack/services/logging/telegram/warming contexts, and related patterns now describe package-based domains, direct executor, repositories, no structlog, and no APScheduler for warming.
- Warming audit (#98–#102 + 6 review bugs): fixed scheduling/ban-risk, board indicators, cycle resilience, localization, channel feedback, reconcile quarantine recovery, cap=1 lockout, trust-ceiling progress, one-sided faded-pair DMs, case-sensitive invite dedup. 536 tests green.
- Accounts QA audit PR #104: 15 findings fixed — single-source RU localization, username=None on unchanged, error snapshot not cached, footer hides on success only, story kind from extension, delete errors translated, search debounce, uploader CSS scoped. 543 tests green.

## Not Yet Built

- Additional user-facing comments page.
- `services/comments.py` / `services/comments/` — not started.
- Comment-generation use of Gemini beyond current generation gateway usage.
- Shared scheduler / `core/scheduler.py` — deliberately not used for warming; only add if a future feature needs true cron scheduling.

## Known Issues

- `aislop --version` can fail on Windows due to a space in the Python path. Use `uv run python -m aislop` if direct CLI invocation fails.

## Open Decisions

Authoritative list of architectural unknowns. Context files may carry `[TO BE DETERMINED]` markers; this section is the single index of all of them.

### Architecture / design

- **Account lifecycle enum beyond session health** — session health is stored on `accounts.status`; warming/runtime lifecycle lives separately in `warming_account_state.state`. A unified business lifecycle is still undecided. (`context/telegram.md`)
- **NiceGUI Logs page pagination** — limit + offset strategy on the SQLite `logs` query. (`context/logging.md`)
- **Project purpose / "why"** — deliberately deferred; not documented anywhere.

### Tooling / process

- **Mutation testing (`mutmut`)** — consider adding once critical modules stabilize. Start as nightly/workflow_dispatch only; do not gate PR merges until signal is proven.
