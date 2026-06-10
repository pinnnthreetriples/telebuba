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
    condition: for APScheduler-specific details
  - target: context/logging.md
    condition: for the loguru / structlog / Sentry layout
last_updated: 2026-06-10
---

# Stack

Pinned in `pyproject.toml` + `uv.lock`. Local dev: Python 3.13.13, uv 0.10.9.

## Runtime

| Package        | Role / non-obvious note |
|----------------|--------------------------|
| `nicegui`      | UI **and** HTTP server in one async process. No separate frontend. |
| `sqlalchemy`   | ORM for SQLite. Only used inside `core/db.py`. |
| `telethon`     | Telegram MTProto client. Only used inside `core/telegram_client.py`. |
| `python-socks` | SOCKS5/HTTP proxy. One proxy per account — without it, ban. |
| `apscheduler`  | In-process scheduler for warming jobs. No external broker. |
| `httpx`        | Async HTTP client for Gemini API calls. |
| `python-dotenv`| Loads `.env` into `core/config.py`. |
| `pydantic`     | Validation at every layer boundary. |
| `anyio`        | Structured concurrency primitives. |

## Logging stack

| Package       | Role |
|---------------|------|
| `loguru`      | Rotating `debug.log` for diagnostic noise (stacktraces, retries, timings). |
| `structlog`   | Structured business events → SQLite `logs` table (queried by the NiceGUI Logs page). |
| `sentry-sdk`  | Production error reporting. ERRORs and unhandled exceptions only. |

Full three-tier architecture: `context/logging.md`.

## Dev / test toolchain

| Package         | Role / non-obvious note |
|-----------------|--------------------------|
| `uv`            | Package manager + venv. Replaces pip/venv. |
| `ruff`          | Lint + format. Replaces black, isort, flake8, pyupgrade, pydocstyle. |
| `ty`            | Type checker from Astral. ~100× mypy speed. Pre-1.0. |
| `pytest`        | Test runner. |
| `pytest-asyncio`| Async test support (Telethon, NiceGUI). |
| `pytest-cov`    | Coverage. |
| `hypothesis`    | Property-based tests — generates pathological inputs. |
| `bandit`        | SAST — SQL injection, `eval`, weak crypto. |
| `pip-audit`     | Known CVEs in dependencies. |
| `semgrep`       | 2000+ rule SAST. Heavy — usually CI-only. |
| `aislop`        | "AI-slop" detector — hallucinated imports, swallowed exceptions, dead code. |
| `radon`         | Cyclomatic complexity (A–F). Below C → split the function. |
| `pre-commit`    | Hook runner — ruff + ty + bandit before every commit. |
| `deptry`        | Unused / missing dependencies. |
| `vulture`       | Dead code detector. |
| `respx`         | HTTP mocking for tests (fake Gemini responses). |
| `factory-boy`   | Fake account / proxy / session fixtures for tests. |

## Deliberately NOT Used

- No separate web framework (FastAPI/Flask) — NiceGUI is the server (FastAPI is a transitive dep, we do not import it directly).
- No external broker (Redis/Celery/RabbitMQ) — APScheduler in-process is enough for ~50 accounts.
- No remote DB (Postgres/MySQL) — SQLite. Migrate if scale demands it.
- No `print()` — `core/logging.py` only.
- No `mypy` — `ty`.
- No `black` / `isort` / `flake8` / `pyupgrade` / `pydocstyle` — all replaced by `ruff`.

## Version Constraints

- Python ≥ 3.13.
- uv ≥ 0.10.
- Other minimums in `pyproject.toml`, exact versions in `uv.lock`.

## Known Issues

- `aislop --version` fails on Windows due to a space in the Python install path. Call via `uv run python -m aislop` if the CLI breaks.
