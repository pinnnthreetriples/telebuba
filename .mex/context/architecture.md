---
last_updated: 2026-07-16
---

# Architecture

```text
React SPA → /api/v1 → api/ → services/ → core/ → SQLite, Telegram, OpenAI/Gemini
                         ↘ schemas/ shared Pydantic contracts
```

- `main.py`: FastAPI composition root, lifespan runtimes, SSE, routers, static SPA.
- `api/`: request/dependency/error/serialization only.
- `services/`: account, auth, proxy, warming, neurocomment, content, dialogue, logs/events, spam/trust policy and orchestration.
- `core/`: repositories/migrations and all external adapters: Telegram, AI, auth, logging/Sentry, SSE, proxy checks.
- `schemas/`: pure contracts; no project-layer imports or I/O.
- `frontend/`: React 19, strict TypeScript, Vite and FSD; reaches Python only over `/api/v1`.

## Import law
| Layer | May import |
|---|---|
| `api/` | `services`, `schemas`, `core.config`, `core.logging`, FastAPI |
| `services/` | `services`, `core`, `schemas` |
| `core/` | `schemas`, stdlib, third-party packages |
| `schemas/` | Pydantic and typing/stdlib only |

Runtime is deliberately single-process: SQLite plus in-process tasks require one uvicorn worker. Cross-layer values are typed; API data is locale-neutral. Business logs go through `core/logging.py`; history is `/api/v1/logs`, live updates are authenticated `/api/v1/events`. `tests/test_architecture.py`, manifests, workflows, and code are the executable source of truth.
