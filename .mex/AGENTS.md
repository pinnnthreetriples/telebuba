---
name: agents
description: Always-loaded project anchor. Read first. Project identity, stack, file map, non-negotiables, commands, pointer to ROUTER.md.
last_updated: 2026-06-16
---

# Telebuba

## What This Is
Telegram account operations dashboard: account/session management, proxy/profile metadata, runtime workflows, logs, and AI-assisted text generation through typed gateways.

## Stack
Python 3.13 · NiceGUI · SQLAlchemy/SQLite · Telethon · httpx · loguru+Sentry · pydantic-settings · uv · ruff · ty · pytest · hypothesis · bandit · pip-audit · semgrep · deptry · vulture · radon · aislop · pre-commit

## File Map
```text
telebuba/
├── main.py                 NiceGUI composition root; registers pages; reconciles/shuts down runtime tasks
├── pyproject.toml          uv project + strict test/lint/security gates
├── .env                    local secrets — gitignored
├── .env.example            committed template; must mirror core/config.py
├── core/                   infrastructure gateways; only layer touching third-party SDKs
│   ├── db.py               shared SQLite plumbing + compatibility re-exports
│   ├── repositories/       per-aggregate DB query modules
│   ├── telegram_client/    Telethon gateway package; public API re-exported from core.telegram_client
│   ├── config.py           pydantic-settings, nested namespaces
│   ├── gemini.py           HTTP gateway for Gemini
│   └── logging.py          loguru + SQLite logs + optional Sentry
├── schemas/                Pydantic models; shared types, no behavior, no I/O
├── services/               business logic; UI-agnostic; no SDK imports
│   ├── accounts/           account/session/profile/proxy operations
│   └── warming/            runtime workflow domain package
├── features/               UI-thin NiceGUI pages/components; delegates to services
│   ├── accounts/
│   ├── warming/
│   └── logs.py
└── tests/                  mirrors source tree; includes architecture/property tests
```

For the live implementation state, read `state/active.md`. This anchor is only the stable routing summary.

## Non-Negotiables (one-line each — full text in `context/conventions.md`)
1. **Feature Isolation (UI-thin)** — one feature = one module or package under `features/`; UI handlers only; business logic delegates to `services/`. No cross-feature imports.
2. **Pydantic Boundaries** — all inter-layer data through Pydantic models in `schemas/`; no raw `dict`/`tuple`/`list`; public funcs return models or `None`.
3. **No Hardcoded Values** — tunables in `core/config.py`, secrets in `.env` via `core/config.py`.
4. **Logging Only** — no `print()`; all logging via `core/logging.py`.
5. **Layer Isolation (4 layers)** — `features/` → `services/` + `core/` + `schemas/`; `services/` → other `services/` + `core/` + `schemas/`; `core/` → `schemas/` + third-party; `schemas/` → `pydantic` + typing/stdlib only. Matrix in `context/architecture.md`.
6. **Gateways** — DB only via `core/db.py` / `core/repositories/*`; Telegram only via `core.telegram_client.execute(account_id, action)` with typed action schemas. `sqlalchemy` / `telethon` forbidden in `services/` and `features/`.
7. **Test Coverage (strict)** — every feature/service change ships tests; warnings → errors; branch coverage ≥ 90%; prefer `/tdd` skill.
8. **Async + Type Safety** — type hints everywhere; `from __future__ import annotations`; I/O is `async def`; `raise X(...) from e`.
9. **Device Fingerprint Immutable** — one profile per account, created at registration, never mutated.
10. **Configuration-Driven** — all limits/delays/proxies through `core/config.py`; nested namespaces (`settings.warming`, `settings.gemini`, ...); no magic numbers.
11. **Services Layer** — all business logic lives in `services/<domain>/` or `services/<domain>.py`. Features validate, call services, render.

## Commands
- Install: `uv sync`
- Dev: `uv run python main.py`
- Test: `uv run pytest`
- Lint / format: `uv run ruff check .` / `uv run ruff format .`
- Types: `uv run ty check .`
- Pre-commit: `uv run pre-commit run --all-files`
- Aislop on Windows: `uv run python -m aislop` if direct CLI invocation fails
- Full toolchain — `context/setup.md`.

## Scaffold Growth
After meaningful work, run GROW (full text in `ROUTER.md` Behavioural Contract):
- **Ground / Record / Orient / Write / Board.** Live state lives in `state/active.md`; board moves per `context/kanban.md`.

## Navigation
Read `ROUTER.md` at session start before any task. ROUTER drives every other context file from the routing table. Shell-command policy → `context/rtk.md`. Skills → `context/skills.md`. CI policy → `context/ci.md`.
