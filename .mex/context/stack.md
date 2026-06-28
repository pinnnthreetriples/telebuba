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
  - target: context/frontend.md
    condition: for the React SPA stack
  - target: context/telegram.md
    condition: for Telethon-specific details
  - target: context/warming.md
    condition: for runtime workflow details
  - target: context/logging.md
    condition: for the loguru / SQLite / Sentry layout
last_updated: 2026-06-28
---

# Stack

Split stack: a **Python backend** (pinned in `pyproject.toml` + `uv.lock`; Python 3.13.x, uv)
exposing a JSON API, and a **React SPA** (`frontend/`, pinned in `package.json`). This file
covers the backend; the frontend stack lives in `context/frontend.md`.

## Backend runtime

| Package | Role / non-obvious note |
| --- | --- |
| `fastapi` | The `/api/v1` JSON API framework. Imported only in `api/` and `main.py`. |
| `uvicorn` | ASGI server. **Single-worker** — the in-process runtimes assume one process. `uvicorn.run(app)` lives in `main.py`. |
| `sqlalchemy` | SQLite access. Only used inside `core/db.py` and `core/repositories/`. |
| `telethon` | Telegram MTProto client. Only used inside `core/telegram_client/`. |
| `python-socks` | SOCKS5/HTTP proxy support for Telethon gateway construction. |
| `httpx` | Async HTTP client for `core/gemini.py`; services do not import it directly. |
| `python-dotenv` | Loaded through `pydantic-settings`; callers use `core.config.settings`. |
| `pydantic` | Validation at every layer boundary; the source of the OpenAPI schema. |
| `pydantic-settings` | Nested settings namespaces from `.env`. |
| `anyio` | Async/concurrency support used by dependencies and tests. |
| `loguru` | Rotating diagnostic log sink, encapsulated by `core/logging.py`. |
| `sentry-sdk` | Optional production error reporting, encapsulated by `core/logging.py`. |
| *(JWT + password-hash libs)* | Used **only** inside `core/auth.py` (token mint/verify, hashing). |

## Backend runtime deliberately not used

| Tool | Reason |
| --- | --- |
| `nicegui` | Removed in the split-stack pivot (2026-06-28). The UI is the React SPA; FastAPI/uvicorn (previously bundled by NiceGUI) are now direct deps. |
| `apscheduler` | Unused for warming (per-account `asyncio.Task`s) and neurocomment (event listener + sweep). Add a scheduler only for true cron semantics. |
| `structlog` | Unused. Structured business events are Pydantic payloads persisted to SQLite via `core.logging.log_event()`. |
| Multi-worker uvicorn | Would duplicate the single-process runtimes and race the DB. Extract runtimes to a separate process first if ever needed. |
| External broker/queue | Not needed for the current single-process architecture. |
| Remote DB | SQLite is sufficient for the current local/single-process scope. |

## Frontend (summary — full law in `context/frontend.md`)

React + TypeScript (strict) + Vite; TanStack Router/Query/Table/Form; Tailwind + shadcn/ui
(Radix); `@hey-api/openapi-ts` generated client; react-i18next + `Intl`; Sentry React;
Vitest + RTL + Playwright; Steiger / eslint-plugin-boundaries for FSD enforcement; self-hosted
Inter + flag-icons (zero CDN).

## Logging stack

| Package / store | Role |
| --- | --- |
| `loguru` | Rotating `debug.log` for diagnostic noise (stacktraces, retries, timings). |
| SQLite `logs` table | Structured business events, served to the SPA via `/api/v1/logs`. |
| `sentry-sdk` | Optional production error reporting for ERROR events/unhandled exceptions. |

Full logging architecture: `context/logging.md`.

## Dev / test toolchain (backend)

| Package | Role / non-obvious note |
| --- | --- |
| `uv` | Package manager + venv. Replaces pip/venv. |
| `ruff` | Lint + format. Replaces black, isort, flake8, pyupgrade, pydocstyle. |
| `ty` | Type checker from Astral. Pre-1.0; strict unresolved-reference rules are enabled. |
| `pytest` | Test runner. |
| `pytest-asyncio` | Async test support. |
| `pytest-cov` | Branch coverage with 90% floor on `api`/`core`/`schemas`/`services`. |
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

The frontend has its own toolchain (ESLint/Prettier/tsc/Vitest/Playwright/Steiger) run from
`frontend/package.json` — see `context/ci.md` for the CI jobs.

## Deliberately NOT Used

- No NiceGUI — the React SPA is the UI.
- No external broker (Redis/Celery/RabbitMQ) in the current architecture.
- No remote DB (Postgres/MySQL) yet.
- No `print()` — `core/logging.py` only.
- No `mypy` — `ty`.
- No `black` / `isort` / `flake8` / `pyupgrade` / `pydocstyle` — all replaced by `ruff`.

## Version Constraints

- Python ≥ 3.13.
- uv ≥ 0.10.
- Other backend minimums in `pyproject.toml`, exact versions in `uv.lock`; frontend versions
  in `frontend/package.json` + lockfile.

## Known Issues

- `aislop --version` can fail on Windows due to a space in the Python install path. Call via
  `uv run python -m aislop` if direct CLI invocation breaks.
</content>
