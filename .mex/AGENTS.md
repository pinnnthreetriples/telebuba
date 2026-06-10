---
name: agents
description: Always-loaded project anchor. Read first. Project identity, stack, file map, non-negotiables (one-liners), commands, pointer to ROUTER.md.
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
├── main.py                 NiceGUI entrypoint (UI + scheduler) — stub today
├── pyproject.toml          uv project + locked deps
├── .env                    secrets — gitignored, not present until first run
├── .env.example            template — planned, not present
├── core/                   shared infrastructure — only layer touching third-party SDKs (planned)
├── schemas/                Pydantic models; shared types, no behavior (planned)
├── features/               one file per user-facing feature; never imports another feature (planned)
└── tests/                  mirrors source tree; canary test only today
```

Files marked `planned` do not exist yet — see `state/active.md` for the live picture.

## Non-Negotiables (one-line each — full text in `context/conventions.md`)
1. **Feature Isolation** — one file per feature in `features/`; never modify existing feature files; no cross-feature imports.
2. **Pydantic Boundaries** — all inter-layer data through Pydantic models in `schemas/`; no raw `dict`/`tuple`/`list`; public funcs return models or `None`.
3. **No Hardcoded Values** — tunables in `core/config.py`, secrets in `.env` via `core/config.py`.
4. **Logging Only** — no `print()`; all logging via `core/logging.py`.
5. **Layer Isolation** — `features/` → `core/` + `schemas/`; `core/` → `schemas/` + third-party; `schemas/` → `pydantic` + `typing` only. Full matrix in `context/architecture.md`.
6. **Gateways** — DB only via `core/db.py`; Telegram only via `core/telegram_client.py`; `sqlalchemy` and `telethon` forbidden in `features/*.py`.
7. **Test Coverage (strict)** — every feature ships a pytest test; warnings → errors; branch coverage ≥ 90 %; prefer `/tdd` skill.
8. **Async + Type Safety** — type hints everywhere; `from __future__ import annotations`; I/O is `async def`; `raise X(...) from e`.
9. **Device Fingerprint Immutable** — one profile per account, created at registration, never mutated.
10. **Configuration-Driven** — all limits / delays / proxies through `core/config.py`; no magic numbers in code.

## Commands
- Install: `uv sync`
- Dev: `uv run python main.py`
- Test: `uv run pytest`
- Lint / format: `uv run ruff check .` / `uv run ruff format .`
- Types: `uv run ty check .`
- Pre-commit: `uv run pre-commit run --all-files`
- Full toolchain — `context/setup.md`.

## Scaffold Growth
After meaningful work, run GROW (full text in `ROUTER.md` Behavioural Contract):
- **Ground / Record / Orient / Write / Board.** Live state lives in `state/active.md`; board moves per `context/kanban.md`.

## Navigation
Read `ROUTER.md` at session start before any task. ROUTER drives every other context file from the routing table. Shell-command policy → `context/rtk.md`. Skills → `context/skills.md`. CI policy → `context/ci.md`.
