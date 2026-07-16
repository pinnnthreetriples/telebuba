---
name: decisions
description: Current architectural decisions; history lives in git and the MEX event log.
triggers: [decision, alternative, rationale, why]
edges:
  - target: context/architecture.md
    condition: structural decision
  - target: context/stack.md
    condition: technology decision
last_updated: 2026-07-16
---

# Decisions
Keep only current decisions here. Use git, merged PRs, and `.mex/events/decisions.jsonl` for history.

- React SPA calls FastAPI through `/api/v1`; production serves `frontend/dist` from `main.py`.
- Uvicorn remains single-worker while SQLite and background runtimes are process-local.
- Backend flow is `api/` → `services/` → `core/`; `schemas/` owns shared Pydantic contracts.
- Routes only validate, call services, and serialize.
- Frontend follows FSD, uses the generated API client, and owns RU/EN presentation.
- External systems are accessed only through `core/` gateways.
- Proxies live in one shared pool; account assignment and capacity are configuration-driven.
- Warming target cadence is bounded by phase and trust policies.
- Neurocomment uses an event listener, atomic claims, quotas, readiness checks, and configured AI providers.
- Telegram actions execute directly; persisted domain state provides restart recovery.
- SQLite and append-only migrations are intentional for the current deployment model.