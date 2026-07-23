---
name: agents
description: Always-loaded project anchor with identity, hard rules, commands, and navigation.
last_updated: 2026-07-17
---

# Telebuba

## What This Is
Telegram operations dashboard for accounts, proxies, warming, neurocomment, profiles, and channels.

## Non-Negotiables
- Preserve `api → services → core` and typed Pydantic boundaries.
- Keep external I/O in `core/`; API and frontend contain no runtime policy.
- Never expose secrets, sessions, tdata, JWTs, or proxy credentials.
- Add tests for behavior changes; test files stay at or below 700 lines.
- Run one uvicorn worker; report only checks actually executed.

## Commands
- Dev: `uv run uvicorn main:app --reload`; frontend: `cd frontend && npm run dev`
- Backend: `uv run pytest`
- Quality: `uv run pre-commit run --all-files`
- Frontend: `cd frontend && npm run gates && npm run build`
- Memory: `npx mex-agent check --quiet`

## GROW
After meaningful work:
- update `.mex/ROUTER.md` only when project state changes;
- update affected `.mex/context/` facts;
- create or improve a `.mex/patterns/` runbook for repeatable work;
- bump `last_updated` and use `mex log` when rationale matters.

## Navigation
Read `.mex/ROUTER.md`, then load only its matching task route and at most one relevant pattern.
