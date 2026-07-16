---
name: architecture
description: System flow and enforced layer boundaries.
triggers: [architecture, flow, layers, imports, integration]
edges:
  - target: context/conventions.md
    condition: implementation rules
  - target: context/frontend.md
    condition: frontend structure
  - target: context/decisions.md
    condition: rationale
last_updated: 2026-07-16
---

# Architecture

```text
React SPA → HTTP /api/v1 → api/ → services/ → core/ → SQLite, Telegram, AI providers
                                  ↘ schemas/ shared Pydantic contracts
```

- `main.py`: FastAPI composition root, lifespan runtimes, routers, SSE, and static SPA serving.
- `api/`: request binding, dependencies, error mapping, serialization. No business logic or direct I/O.
- `services/`: business rules, state transitions, orchestration, and runtime ownership.
- `core/`: repositories/migrations, Telegram, LLM, auth, logging, SSE, proxy and other adapters.
- `schemas/`: pure Pydantic contracts; no project-layer imports or I/O.
- `frontend/`: independent React/FSD tree; reaches Python only through `/api/v1`.

## Import law
| Layer | May import |
|---|---|
| `api/` | `services`, `schemas`, `core.config`, `core.logging`, FastAPI |
| `services/` | `services`, `core`, `schemas` |
| `core/` | `schemas`, stdlib, third-party packages |
| `schemas/` | Pydantic and typing/stdlib only |

All cross-layer values use Pydantic models. Database, Telegram, provider, auth, logging, and event access goes through the owning `core/` gateway. API responses are locale-neutral. Run one uvicorn worker while runtimes and SQLite remain process-local. `tests/test_architecture.py` is the executable source of truth for boundaries.