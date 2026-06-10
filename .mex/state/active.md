---
name: active-state
description: Live project state — what works, what is not yet built, known issues. Updated by the agent in the Record step of GROW after meaningful work.
last_updated: 2026-06-10
---

# Active State

This file is the only place that should change after every task. `ROUTER.md` stays stable.

## Working
- `pyproject.toml` + `uv.lock` resolved; `.venv` on Python 3.13.13.
- Full stack installed and import-verified: nicegui, sqlalchemy, telethon, python-socks, apscheduler, httpx, python-dotenv, pydantic, loguru, structlog, sentry-sdk, anyio.
- Dev toolchain installed and CLI-verified: ruff, ty, pytest+asyncio+cov, hypothesis, bandit, pip-audit, semgrep, deptry, vulture, radon, pre-commit, respx, factory-boy, aislop.
- `main.py` — placeholder `Hello from telebuba!` from `uv init`.
- `.gitignore` covers `.env`, `*.session`, `*.db`, `*.log`, tool caches.
- `pyproject.toml` configured for maximum test strictness: `filterwarnings=["error"]`, `--strict-markers`, `--strict-config`, `xfail_strict`, `asyncio_mode=strict`, branch coverage with `--cov-fail-under=90`.
- `conftest.py` registers Hypothesis profiles (`strict` = 200 examples, default; `dev` = 50 examples, fast loop).
- `ruff` configured with `select = ["ALL"]` + minimal ignores; `ty` strict imports/references; `bandit`, `deptry`, `vulture` wired in pyproject.
- `.pre-commit-config.yaml` installed and verified end-to-end (13 hooks: generic hygiene + gitleaks + ruff + ruff-format + bandit + ty). `pre-commit install` ran — git hook is wired in `.git/hooks/pre-commit`.
- `main.py` replaced with a minimal valid stub that satisfies the non-negotiables (no `print`, return type annotation, `from __future__ import annotations`). Real NiceGUI bootstrap lands with the first feature.

## Not Yet Built
- `core/config.py`, `core/db.py`, `core/telegram_client.py`, `core/logging.py`.
- `features/` (accounts, warming, comment generation, logs page, etc.).
- `schemas/`.
- Real NiceGUI entrypoint in `main.py`.
- SQLAlchemy models (including the `logs` table).
- APScheduler wiring (either a `core/scheduler.py` or owned by `features/warming.py`).
- `.env.example`.
- AI provider integration — Gemini API planned (`GEMINI_API_KEY` reserved).

## Known Issues
- `aislop --version` fails on Windows due to a space in the Python path — call via `uv run python -m aislop` instead.
- Open: where to store the `account_id → proxy` mapping. See `context/telegram.md`.

## Open Decisions
- Project purpose / "why" — not documented (deliberately deferred).
- Pagination strategy for the NiceGUI Logs page.
- Whether APScheduler's own jobstore points at the same SQLite file.
