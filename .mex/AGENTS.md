---
name: agents
description: Always-loaded project anchor.
last_updated: 2026-07-16
---

# Telebuba
Telegram operations dashboard with account/session management, proxy pool, warming, neurocomment, and profile/channel tools.

## Rules
- Read `ROUTER.md`; load only task-specific context.
- Preserve `api → services → core` boundaries and Pydantic contracts.
- Use core gateways for DB, Telegram, providers, auth, logging, and SSE.
- Add tests for behavior changes; backend coverage ≥90%; test files ≤700 lines.
- Run one uvicorn worker; never expose secrets or session files.

## Commands
`uv run pytest` · `uv run ruff check .` · `uv run ty check .` · `uv run python tools/aislop_gate.py` · `cd frontend && npm run gates && npm run build`

After meaningful work, update current state only if reality changed and use `mex log` for durable rationale.