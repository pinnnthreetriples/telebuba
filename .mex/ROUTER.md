---
name: router
description: Session bootstrap, current project state, routing table, and compact behavioural contract.
edges:
  - target: context/architecture.md
    condition: system design, integrations, or layer boundaries
  - target: context/conventions.md
    condition: writing or reviewing backend code
  - target: context/frontend.md
    condition: writing or reviewing frontend code
  - target: context/decisions.md
    condition: making or revisiting an architectural decision
  - target: context/setup.md
    condition: installation, local run, or environment problems
  - target: patterns/INDEX.md
    condition: before implementing a recurring task type
last_updated: 2026-07-16
---

# Session Bootstrap

Read `.mex/AGENTS.md`, then this file. Load only the task-specific files below; do not preload the whole scaffold.

## Current Project State

**Working:**
- React 19 SPA over FastAPI `/api/v1`; generated OpenAPI client; RU/EN i18n; cookie auth.
- Account/session import and phone login, proxy pool, profile media, stories, music, and owned-channel management.
- Per-account warming runtime with personas, fleet de-correlation, persisted restart recovery, logs, and safety gates.
- Event-driven neurocomment campaigns with listener, quotas, challenge/vision solver, deletion checks, and semantic dedup.
- Strict CI: backend tests/coverage/security, frontend gates/build, API drift, nightly Hypothesis/Semgrep/mutation.

**Not built / intentionally deferred:**
- Public landing page (`#237`).
- Multi-worker/runtime-worker architecture and remote database.
- Full operator/deployment/backup documentation.

**Known issues:**
- Warming daily action cap may be exceeded after a mid-cycle restart (`#208`).
- SQLite and in-process runtimes require one uvicorn worker.
- Domain context and ADR statuses need periodic sync after fast product changes.

History belongs in git, merged PRs, and `.mex/events/decisions.jsonl`, not in this snapshot.

## Routing Table

| Task | Load |
|---|---|
| System flow, folders, imports | `context/architecture.md` |
| Libraries and versions | `context/stack.md` |
| Backend rules and test policy | `context/conventions.md` |
| Frontend/FSD/i18n | `context/frontend.md` |
| Business services | `context/services.md` |
| Telegram gateway | `context/telegram.md` |
| Warming runtime | `context/warming.md` |
| Neurocomment runtime | `context/neurocomment.md` |
| Proxy pool | `context/proxy.md` |
| Logs and SSE | `context/logging.md` |
| CI/workflows | `context/ci.md` |
| Setup/run commands | `context/setup.md` |
| Architecture rationale | `context/decisions.md` |
| GitHub Project workflow | `context/kanban.md` |
| Shell-output wrapper | `context/rtk.md` |
| Agent skills | `context/skills.md` |
| Repeatable implementation task | `patterns/INDEX.md` |

## Layer Check

```text
frontend/ → HTTP /api/v1 → api/ → services/ → core/
                              ↘ schemas/ shared contracts
```

- `api/`: request binding and error mapping only.
- `services/`: business policy and orchestration.
- `core/`: DB, Telegram, LLM, auth, logging, SSE and other infrastructure gateways.
- `schemas/`: pure Pydantic contracts.

## Behavioural Contract

1. **CONTEXT** — run `npx mex-agent check --quiet`; load the routed context and matching pattern.
2. **BUILD** — make the smallest coherent change in the owning layer.
3. **VERIFY** — run relevant tests and gates; do not claim unrun checks passed.
4. **DEBUG** — reproduce failures and add a regression test where feasible.
5. **GROW** — update this state only when reality changed; refresh affected context/patterns; `mex log` durable rationale; keep history out of this file.
