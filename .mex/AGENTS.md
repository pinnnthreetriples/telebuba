---
name: agents
description: Tiny always-loaded project anchor. Read ROUTER.md for state and task-specific context.
last_updated: 2026-07-16
---

# Telebuba

## What This Is
Telegram account operations dashboard for sessions, proxies, profiles, warming, channel management, logs, and AI-assisted commenting.

## Non-Negotiables
- `api/` stays thin: validate → service → serialize; no DB, Telethon, or business logic.
- Cross-layer contracts are Pydantic models from `schemas/`; SDK access stays in `core/` gateways.
- Configuration comes from typed settings; never commit secrets or session credentials.
- Every behavior change ships tests; backend branch coverage ≥90%, frontend gates must pass.
- Uvicorn stays single-worker while SQLite and runtime tasks remain in-process.

## Commands
- Backend: `uv sync`; `uv run uvicorn main:app --reload`; `uv run pytest`; `uv run pre-commit run --all-files`
- Frontend: `cd frontend && npm ci`; `npm run dev`; `npm run gates`; `npm run build`
- MEX: `npx mex-agent check`; `npx mex-agent sync`; `npx mex-agent doctor`

## Scaffold Growth
After meaningful work, update current state in `ROUTER.md`, surgically refresh relevant `context/` or `patterns/`, bump `last_updated`, and use `mex log` for durable rationale.

## Navigation
Read `ROUTER.md` at session start. Load only the context files routed for the current task.
