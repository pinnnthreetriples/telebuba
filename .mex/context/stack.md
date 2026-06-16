---
name: stack
description: Technology stack and the reasoning behind non-obvious choices. Load when picking a library, deciding between alternatives, or troubleshooting a known tool.
triggers:
  - "library"
  - "package"
  - "dependency"
  - "which tool"
  - "technology"
edges:
  - target: context/decisions.md
    condition: when the why behind a tech choice is needed
  - target: context/conventions.md
    condition: when the question is how to use a library in this codebase
  - target: context/telegram.md
    condition: for Telethon-specific details
  - target: context/warming.md
    condition: for runtime workflow details
  - target: context/logging.md
    condition: for the loguru / SQLite / Sentry layout
last_updated: 2026-06-16
---

# Stack

Pinned in `pyproject.toml` + `uv.lock`. Local dev target: Python 3.13.x, uv.

## Runtime

| Package | Role / non-obvious note |
| --- | --- |
| `nicegui` | UI and HTTP server in one async process. No separate frontend. |
| `sqlalchemy` | SQLite access. Only used inside `core/db.py` and `core/repositories/*`. |
| `telethon` | Telegram MTProto client. Only used inside `core/telegram_client/`. |
| `python-socks` | SOCKS5/HTTP proxy support for Telethon gateway construction. |
| `httpx` | Async HTTP client for `core/gemini.py`; services do not import it directly. |
| `python-dotenv` | Loaded through `pydantic-settings`; callers use `core.config.settings`. |
| `pydantic` | Validation at every layer boundary. |
| `pydantic-settings` | Nested settings namespaces from `.env`. |
| `anyio` | Async/concurrency support used by dependencies and tests. |
| `loguru` | Rotating diagnostic log sink, encapsulated by `core/logging.py`. |
| `sentry-sdk` | Optional production error reporting, encapsulated by `core/logging.py`. |

## Runtime deliberately not used

| Tool | Reason |
| --- | --- |
| `apscheduler` | Removed/unused for the current warming runtime. Per-account runtime work uses raw `asyncio.Task`s. Add a scheduler only if a future feature needs true cron semantics. |
| `structlog` | Removed/unused. Structured business events are Pydantic payloads persisted to SQLite through `core.logging.log_event()`. |
| External broker/queue | Not needed for the current single-process architecture. Revisit only for multi-process execution. |
| Remote DB | SQLite is sufficient for the current local/single-process scope. |

## Logging stack

| Package / store | Role |
| --- | --- |
| `loguru` | Rotating `debug.log` for diagnostic noise (stacktraces, retries, timings). |
| SQLite `logs` table | Structured business events queried by the NiceGUI Logs page. |
| `sentry-sdk` | Optional production error reporting for ERROR events/unhandled exceptions. |

Full logging architecture: `context/logging.md`.

## Dev / test toolchain

| Package | Role / non-obvious note |
| --- | --- |
| `uv` | Package manager + venv. Replaces pip/venv. |
| `ruff` | Lint + format. Replaces black, isort, flake8, pyupgrade, pydocstyle. |
| `ty` | Type checker from Astral. Pre-1.0; strict unresolved-reference rules are enabled. |
| `pytest` | Test runner. |
| `pytest-asyncio` | Async test support. |
| `pytest-cov` | Branch coverage with 90% floor. |
| `hypothesis` | Property-based tests. |
| `bandit` | SAST for Python source. |
| `pip-audit` | Known CVEs in dependencies. |
| `semgrep` | SAST on PR/push. |
| `aislop` | AI-slop/quality gate; zero-tolerance in CI and pre-push. |
| `radon` | Cyclomatic complexity gate; project wrapper fails rank D+. |
| `pre-commit` | Hook runner. |
| `deptry` | Unused / missing / transitive dependencies. |
| `vulture` | Dead code detector. |
| `respx` | HTTP mocking for tests. |
| `factory-boy` | Test factories. |

## Deliberately NOT Used

- No separate web framework in app code (FastAPI is a transitive dependency through NiceGUI; do not import it directly).
- No external broker (Redis/Celery/RabbitMQ) in the current architecture.
- No remote DB (Postgres/MySQL) yet.
- No `print()` — `core/logging.py` only.
- No `mypy` — `ty`.
- No `black` / `isort` / `flake8` / `pyupgrade` / `pydocstyle` — all replaced by `ruff`.

## Version Constraints

- Python ≥ 3.13.
- uv ≥ 0.10.
- Other minimums in `pyproject.toml`, exact versions in `uv.lock`.

## Known Issues

- `aislop --version` can fail on Windows due to a space in the Python install path. Call via `uv run python -m aislop` if direct CLI invocation breaks.
