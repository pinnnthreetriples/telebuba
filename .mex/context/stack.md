---
name: stack
description: Runtime and development technology map.
triggers: [stack, library, dependency, package, tool]
edges:
  - target: context/architecture.md
    condition: component placement
  - target: context/frontend.md
    condition: frontend stack
  - target: context/ci.md
    condition: tool gates
last_updated: 2026-07-16
---

# Stack

## Runtime
- Python 3.13, uv, FastAPI/uvicorn, Pydantic/settings, SQLAlchemy/SQLite.
- Telethon and python-socks for Telegram/account connectivity.
- httpx-backed Gemini/OpenAI gateways.
- loguru plus optional Sentry.
- React 19, strict TypeScript, Vite, TanStack Router/Query/Table/Form, Tailwind, react-i18next, generated hey-api client.

## Quality
Pytest/pytest-asyncio/coverage/Hypothesis, Ruff, ty, pre-commit, Bandit, pip-audit, Semgrep, deptry, vulture, radon, aislop; frontend uses Steiger, ESLint, Prettier, TypeScript, Vitest/RTL.

## Constraints
- Exact versions: `uv.lock` and frontend lockfile; dependency declarations: `pyproject.toml` and `frontend/package.json`.
- One uvicorn worker while SQLite and runtimes are process-local.
- No NiceGUI, APScheduler, broker, remote DB, mypy, black, or direct SDK use outside gateways.
- Run aislop through `uv run python tools/aislop_gate.py`.