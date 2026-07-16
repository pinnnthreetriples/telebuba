---
name: router
description: Session bootstrap, live state, and task routing.
edges:
  - target: context/architecture.md
    condition: system flow or layer boundaries
  - target: context/conventions.md
    condition: backend implementation or review
  - target: context/frontend.md
    condition: frontend implementation or review
  - target: context/decisions.md
    condition: architectural rationale
  - target: context/setup.md
    condition: setup or local commands
  - target: patterns/INDEX.md
    condition: repeatable implementation task
last_updated: 2026-07-16
---

# Session Bootstrap
Read `.mex/AGENTS.md`, then load only the files needed for the task.

## Current State
- **Working:** React/FastAPI split stack; accounts, sessions, proxy pool, profile media, stories, music, owned channels; warming personas/runtime; neurocomment campaigns/listener/vision solver; strict CI/nightly.
- **Deferred:** public landing `#237`, worker/remote-DB architecture, complete operator/deployment docs.
- **Known:** warming daily cap can exceed after a mid-cycle restart (`#208`); run one uvicorn worker.
- **History:** git, merged PRs, and `.mex/events/decisions.jsonl` only.

## Route
| Task | Load |
|---|---|
| Flow, layers, imports | `context/architecture.md` |
| Libraries | `context/stack.md` |
| Backend rules | `context/conventions.md` |
| Frontend | `context/frontend.md` |
| Services | `context/services.md` |
| Telegram | `context/telegram.md` |
| Warming | `context/warming.md` |
| Neurocomment | `context/neurocomment.md` |
| Proxy | `context/proxy.md` |
| Logs/SSE | `context/logging.md` |
| CI | `context/ci.md` |
| Setup | `context/setup.md` |
| Rationale | `context/decisions.md` |
| Board / shell / skills | `context/kanban.md`, `context/rtk.md`, `context/skills.md` |
| Repeatable task | `patterns/INDEX.md` |

## Contract
1. Run `npx mex-agent check --quiet`; load routed context and a matching pattern.
2. Change the smallest coherent unit in its owning layer.
3. Run relevant gates; never claim an unrun check passed.
4. Update this snapshot only when current reality changes; put durable rationale in `mex log`.