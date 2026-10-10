---
name: agents
description: Small always-loaded Telebuba anchor: hard rules, commands, and memory routing.
last_updated: 2026-10-10
---

# Telebuba

Telegram operations dashboard for accounts, proxies, warming, neurocomment, neuroshilling, profiles, and channels.

## Hard rules
- Preserve `api → services → core`; external I/O stays in `core/`. Coding rules: `CODING_STANDARDS.md`.
- Never expose secrets, sessions, tdata, JWTs, or proxy credentials.
- Behavior changes ship with tests. Run one uvicorn worker.
- Report only checks actually executed.

## Commands
- Backend: `uv run pytest`; quality: `uv run pre-commit run --all-files`.
- Frontend: `cd frontend && npm run gates && npm run build`.
- Memory: `npx --yes mex-agent@0.7.1 check --quiet`.
- Setup, CI and pre-push commands: `.mex/context/setup.md`.

## Memory
Read `.mex/ROUTER.md`, then only the matching context and at most one pattern. Run the MEX check at task end. Memory answers WHY; the code graph answers WHERE (`mex graph scope`, `query where-defined`).
