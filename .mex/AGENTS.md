---
name: agents
description: Always-loaded project anchor. Read this first. Project identity, stack, file map, non-negotiables, commands, and pointer to ROUTER.md.
last_updated: 2026-06-10
---

# Telebuba

## What This Is
Telegram account farm manager: creates accounts, warms them up with human-like activity, and generates AI comments.

## Stack
Python 3.13 · NiceGUI · SQLAlchemy/SQLite · Telethon · APScheduler · httpx · loguru+structlog+Sentry · uv · ruff · ty · pytest

## File Map
```
telebuba/
├── main.py                 NiceGUI entrypoint (UI + scheduler)
├── pyproject.toml          uv project + locked deps
├── .env                    secrets (gitignored)
├── core/                   shared infrastructure — the only layer touching third-party SDKs
│   ├── config.py             pydantic + python-dotenv; single source of truth
│   ├── db.py                 SQLAlchemy gateway (only place sqlalchemy is imported)
│   ├── telegram_client.py    Telethon gateway (only place telethon is imported)
│   └── logging.py            loguru + structlog + Sentry (only place these are imported)
├── schemas/                Pydantic models; shared types, no behavior
├── features/               one file per user-facing feature; never imports another feature
└── tests/                  mirrors source tree
```

## Non-Negotiables (one line each — full text in `context/conventions.md`)
1. Feature isolation — one feature per file in `features/`; never modify existing feature files.
2. No cross-feature imports — `features/a.py` must not import `features/b.py`.
3. Pydantic at every layer boundary — no raw `dict`/`tuple`/`list` crossing layers.
4. No hardcoded values — tunables in `core/config.py`, secrets in `.env`.
5. No `print()` — `core/logging.py` only.
6. Layer isolation — see import matrix in `context/architecture.md`.
7. Gateways — DB only via `core/db.py`; Telegram only via `core/telegram_client.py`.
8. Test coverage — every new feature ships with a `tests/test_*.py`.
9. Async + type safety — type hints on every function; `from __future__ import annotations`; I/O is `async def`; `raise X(...) from e`.
10. Device fingerprint immutable — one profile per account, created once, never mutated.
11. Configuration-driven — no magic numbers; all tunables in `core/config.py`.

## Commands
- Install: `uv sync`
- Dev: `uv run python main.py`
- Test: `uv run pytest`
- Lint / format: `uv run ruff check .` / `uv run ruff format .`
- Types: `uv run ty check .`
- Pre-commit (all hooks): `uv run pre-commit run --all-files`
- Full toolchain — see `context/setup.md`.

## Scaffold Growth
After meaningful work, run GROW:
- **Ground:** what changed in reality?
- **Record:** update `ROUTER.md`'s pointer to `state/active.md`; update relevant `context/` files.
- **Orient:** create or update a `patterns/` runbook if this can recur.
- **Write:** bump `last_updated` on changed files; `mex log` when rationale matters.

## Navigation
Read `ROUTER.md` at session start before any task. Live project state lives in `state/active.md`. Work is picked from the GitHub Project board — protocol in `context/kanban.md` (load this every session too).
