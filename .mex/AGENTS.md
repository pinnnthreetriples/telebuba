---
name: agents
description: Always-loaded project anchor. Read first. Project identity, stack, file map, non-negotiables, commands, pointer to ROUTER.md.
last_updated: 2026-07-06
---

# Telebuba

## What This Is
Telegram account operations dashboard: account/session management, proxy/profile metadata, runtime workflows, logs, and AI-assisted text generation through typed gateways.

## Stack
**Backend:** Python 3.13 · FastAPI + uvicorn (single-worker) · SQLAlchemy/SQLite · Telethon · httpx · loguru+Sentry · pydantic-settings · uv · ruff · ty · pytest · hypothesis · bandit · pip-audit · semgrep · deptry · vulture · radon · aislop · pre-commit
**Frontend (`frontend/`):** React + TypeScript (strict) · Vite · TanStack Router/Query/Table/Form · Tailwind + shadcn/ui · `@hey-api/openapi-ts` · react-i18next · Sentry React · Vitest · Steiger boundary-lint (full law in `context/frontend.md`)

## File Map
```text
telebuba/
├── main.py        FastAPI/uvicorn composition root: lifespan (warming+neurocomment runtimes), /api/v1 routers, StaticFiles → frontend/dist; single-worker
├── api/           UI-thin top layer: v1/ routers (one per domain + SSE events.py), deps.py, errors.py (error envelope {error:{code,message,fields?}})
├── core/          infrastructure gateways — the ONLY layer touching third-party SDKs: db.py+_schema_tables.py, migrations*+migration_steps*, config.py+_config_domains.py, repositories/ (per-aggregate DB queries), telegram_client/ (Telethon gateway: execute + typed actions), gemini.py, openai.py, auth.py (JWT), events.py (SSE pub/sub), logging.py, device_fingerprint.py, phone_geo.py, proxy_check.py, tdata_import.py
├── schemas/       Pydantic models; shared types, no behavior, no I/O (api.py: error envelope + Page[T])
├── services/      business logic; UI-agnostic; no SDK imports — accounts/, warming/, neurocomment/, auth/, proxies.py, content.py, dialogues.py, logs.py, events.py, spam_status.py, trust.py
├── frontend/      React+TS (strict) + Vite SPA, Feature-Sliced Design; generated hey-api client in src/shared/api; design tokens in tailwind.config; full law in context/frontend.md
├── tests/         mirrors the source tree; architecture/property tests
└── pyproject.toml uv project + strict gates · .env (gitignored) · .env.example (must mirror core/config.py)
```

For the live implementation state, read `state/active.md`. Per-module detail lives in the code
and in the `context/` files (architecture/services/warming/neurocomment).

## Non-Negotiables (one-line each — full text in `context/conventions.md`; frontend law in `context/frontend.md`)
1. **API Layer is UI-thin (`api/`)** — `/api/v1` routes only validate → call a service → serialize; no business logic, no DB/Telegram access. `api/` imports **only** `services`/`schemas`/`core.config`/`core.logging`/`fastapi`.
2. **Pydantic Boundaries** — all inter-layer data through Pydantic models in `schemas/`; no raw `dict`/`tuple`/`list`; public funcs return models or `None` (lists via `Page[T]`).
3. **No Hardcoded Values (backend)** — tunables in `core/config.py`, secrets in `.env` via `core/config.py`. Frontend config via Vite `VITE_*`.
4. **Logging Only** — no `print()`; all logging via `core/logging.py`.
5. **Layer Isolation** — `api/` → `services`/`schemas`/`core.config`/`core.logging`/`fastapi`; `services/` → other `services/` + `core/` + `schemas/`; `core/` → `schemas/` + third-party; `schemas/` → `pydantic` + typing/stdlib only. Frontend is a separate tree reaching the backend only over `/api/v1`. Matrix in `context/architecture.md`.
6. **Gateways** — DB only via `core/db.py` and `core/repositories/`; Telegram only via `core.telegram_client.execute(account_id, action)` with typed action schemas. `sqlalchemy` / `telethon` forbidden in `services/` and `api/`.
7. **Test Coverage (strict)** — every endpoint/service change ships tests; warnings → errors; backend branch coverage ≥ 90%; frontend Vitest ≥ 80%; prefer `/tdd` skill.
8. **Async + Type Safety** — type hints everywhere; `from __future__ import annotations`; I/O is `async def`; `raise X(...) from e`.
9. **Device Fingerprint Immutable** — one profile per account, created at registration, never mutated.
10. **Configuration-Driven (backend)** — all limits/delays/proxies through `core/config.py`; nested namespaces (`settings.warming`, `settings.gemini`, `settings.api`, `settings.auth`, ...); no magic numbers.
11. **Services Layer** — all business logic lives in `services/<domain>/` or `services/<domain>.py`. `api/` routes validate, call services, serialize.
12. **Locale-neutral API** — responses carry codes/enums + ISO-8601 timestamps, never pre-translated text; the SPA owns all i18n.

Before adding files, follow `.mex/context/conventions.md` → **File Placement Guide** (where each kind of code goes, when to split, the package-root rule).

## Commands
- Install (backend): `uv sync`
- Dev (backend API): `uv run uvicorn main:app --reload` (single-worker)
- Test (backend): `uv run pytest`
- Lint / format: `uv run ruff check .` / `uv run ruff format .`
- Types: `uv run ty check .`
- Pre-commit: `uv run pre-commit run --all-files`
- Aislop gate: `uv run python tools/aislop_gate.py` (aislop is an npm tool; `python -m aislop` does not work)
- Regenerate the API client: `uv run python -m tools.gen_api` (dumps OpenAPI → hey-api → prettier; CI drift-checks it)
- Frontend (from `frontend/`): `npm install`; `npm run dev` (vite + `/api` proxy); `npm run gates` (eslint/prettier/boundaries/tsc/vitest)
- Full toolchain — `context/setup.md`.

## Scaffold Growth
After meaningful work, run GROW (full text in `ROUTER.md` Behavioural Contract):
- **Ground / Record / Orient / Write / Board.** Live state lives in `state/active.md`; board moves per `context/kanban.md`.

### Memory hygiene
1. **GROW before every PR** — update `state/active.md` and bump `last_updated` *before* opening the PR, not after merge.
2. **New top-level package = new File Map line** — the map stays at directory level; per-module detail belongs to `context/` files and the code itself.
3. **`.serena/` is deprecated** — do not use it; `.mex/` is the single source of truth. If Serena regenerates files there, delete them or add a deprecation header.
4. **One skill, one source** — edit `.claude/skills/` first, then sync to `.agents/skills/`.

## Skills
Project-local skills in `.claude/skills/` (matt-pocock). Full triggers in `context/skills.md`.

- `/tdd` — red-green-refactor; **mandatory** for any new feature or reproducible bug fix (non-negotiable #7).
- `/diagnose` — reproduce → hypothesise → instrument → fix; use when something is broken or throwing.
- `/prototype` — throwaway exploration before committing to a data model or UI; lives outside production tree.
- `/improve-codebase-architecture` — find deepening opportunities; run before a refactor.
- `/grill-with-docs` — stress-test a plan against `.mex/` context; use before any cross-layer change.
- `/zoom-out` — orientation map for an unfamiliar area; use before acting in unseen code.
- `/to-prd` — turn a conversation into a PRD on the issue tracker.
- `/to-issues` — split a plan into independently-grabbable board items → `Backlog`.

## Session Start
Before a coding task: `npx mex-agent check --quiet`; on drift errors, `npx mex-agent sync --dry-run` and fix the flagged `.mex/` files before coding. Full codebase brief (first session / after major changes): `npx mex-agent init`.

## Navigation
Consult `ROUTER.md` when entering an unfamiliar area or before cross-layer work — it routes to every context file. Shell-command policy → `context/rtk.md`. Skills → `context/skills.md`. CI policy → `context/ci.md`.
