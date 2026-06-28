---
name: setup
description: Dev environment setup and commands. Load when setting up the project for the first time or when environment issues arise.
triggers:
  - "setup"
  - "install"
  - "environment"
  - "getting started"
  - "how do I run"
  - "local development"
edges:
  - target: context/stack.md
    condition: when specific technology versions or library details are needed
  - target: context/architecture.md
    condition: when understanding how components connect during setup
  - target: context/logging.md
    condition: when configuring Sentry DSN or log file location
last_updated: 2026-06-28
---

# Setup

## Prerequisites

- Python 3.13 (dev machine on 3.13.13)
- `uv` 0.10+ (https://github.com/astral-sh/uv)
- Telegram API credentials (api_id + api_hash from my.telegram.org)
- One SOCKS5/HTTP proxy per account
- Gemini API key

## First-time Setup

1. `uv sync` — installs the full stack from `pyproject.toml` into `.venv`.
2. Copy `.env.example` → `.env` and fill in keys (see below).
3. `uv run pre-commit install` — installs the git hooks (already wired on this machine; new clones need to run it once).
4. SQLite tables are created lazily by `core/db.py` on first DB access.
5. `uv run uvicorn main:app --reload` — starts the FastAPI/uvicorn API (single-worker; runtimes reconcile via lifespan).
6. Frontend (from `frontend/`): `npm install` then `npm run dev` — Vite dev server with a `/api` proxy to uvicorn.

## Environment Variables

Uses double-underscore namespace convention (`NAMESPACE__FIELD`) via `pydantic-settings`. Full list in `.env.example`; key ones:

- `TELEGRAM__API_ID` (required) — Telegram API id from my.telegram.org.
- `TELEGRAM__API_HASH` (required) — Telegram API hash.
- `TELEGRAM__SESSION_DIR` (optional) — Telethon session directory, default `sessions`.
- `DB__PATH` (optional) — SQLite file path, default `telebuba.db`.
- `GEMINI__API_KEY` (required) — key for httpx → Gemini text generation.
- `LOGGING__SENTRY_DSN` (optional) — if set, errors are sent to Sentry; otherwise local-only.
- `API__PORT` (optional) — uvicorn port, default 8080.
- `AUTH__SECRET` (required) — JWT signing secret (see `settings.auth`).

All namespaces: `TELEGRAM__`, `API__`, `AUTH__`, `DB__`, `PROXY__`, `PROFILE_MEDIA__`, `LOGGING__`, `WARMING__`, `GEMINI__`, `TRUST__`. See `core/config.py` for the full nested settings model. (`UI__*` was retired with NiceGUI in the split-stack pivot; frontend config is `VITE_*`.)

## Common Commands

- `uv sync` — install / refresh dependencies from `pyproject.toml` + `uv.lock`.
- `uv add <pkg>` / `uv add --dev <pkg>` — add a runtime / dev dependency.
- `uv run uvicorn main:app --reload` — run the API (single-worker; runtimes reconcile via lifespan).
- `uv run pytest` — full test suite. Strict mode is baked into `pyproject.toml`: warnings → errors, branch coverage ≥ 90%, `asyncio_mode = strict`, Hypothesis `strict` profile (200 examples).
- `uv run pytest -p no:cacheprovider --hypothesis-profile=dev` — fast inner-loop run (50 Hypothesis examples, fresh cache).
- `uv run ruff check .` — lint.
- `uv run ruff format .` — format.
- `uv run ty check .` — type check.
- `uv run bandit -r .` — security (SAST).
- `uv run pip-audit` — known CVEs in dependencies.
- `uv run semgrep --config auto .` — security rules (slow, usually CI).
- `uv run vulture .` — dead code.
- `uv run deptry .` — unused / missing dependencies.
- `uv run radon cc -a .` — complexity (A–F).
- `uv run python -m aislop .` — AI-slop detector (see Known Issues — the `aislop` CLI is broken on Windows, hence `python -m`).
- `uv run pre-commit run --all-files` — run all hooks manually.

## Common Issues

**`aislop --version` complains about a space in the path** — on Windows the `aislop.exe` wrapper script does not quote the Python path correctly when it contains a space. Workaround: `uv run python -m aislop ...`.

**`uv sync` after changing Python version** — if you changed `.python-version`, delete `.venv` and re-run `uv sync`.

[More issues — TO BE DETERMINED after first real runs. Expected: Telethon session file locking, "database is locked" under concurrent async tasks, uvicorn port conflicts, Vite/uvicorn dev-proxy CORS.]
