---
name: agents
description: Small always-loaded Telebuba anchor: hard rules, commands, and memory routing.
last_updated: 2026-08-06
---

# Telebuba

Telegram operations dashboard for accounts, proxies, warming, neurocomment, profiles, and channels.

## Hard rules
- Preserve `api → services → core` and typed Pydantic boundaries; external I/O stays in `core/`.
- Never expose secrets, sessions, tdata, JWTs, or proxy credentials.
- Add tests for behavior changes; test sources stay at or below 700 lines.
- Run one uvicorn worker and report only checks actually executed.

## Commands
- Backend: `uv run pytest`; quality: `uv run pre-commit run --all-files`.
- Frontend: `cd frontend && npm run gates && npm run build`.
- Memory: `npx --yes mex-agent@0.7.1 check --quiet`.
- Setup, CI and pre-push commands live in `.mex/context/setup.md`.

## Memory discipline
Read `.mex/ROUTER.md`, then only the matching context and at most one relevant pattern.
At task end, run the MEX check. Update durable current facts only in the matching context, recurring workflows in patterns, and decision rationale with `mex log`; keep bug/PR history, counts, benchmarks and transient state out of routed memory.
Routed-memory size budgets are enforced by MEX CI: router ≤2500 bytes, each context ≤5000, each pattern ≤3000.
