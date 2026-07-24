---
name: router
description: Session bootstrap, current project state, task routing, and MEX work lifecycle.
edges:
  - target: context/architecture.md
    condition: backend flow, stack, services, gateways, or system design
  - target: context/conventions.md
    condition: backend implementation or review
  - target: context/frontend.md
    condition: React, FSD, TypeScript, i18n, or frontend gates
  - target: context/runtime.md
    condition: Telegram, proxy, warming, or neurocomment runtime
  - target: context/setup.md
    condition: setup, commands, CI, or verification
  - target: patterns/INDEX.md
    condition: repeatable implementation task
last_updated: 2026-07-24
---

# Telebuba Router

## State
- Working: React/FastAPI; accounts, sessions, proxy pool, profile media, channels, warming runtime, neurocomment listener and vision solver, strict CI.
- Deferred: landing #237, worker/remote DB architecture, full operator and deployment documentation, persistent neurocomment post queue + catch-up, send↔DB idempotency reconciliation, backup readiness/off-site.
- Known: warming daily cap may undercount after a mid-cycle restart (#208); use one uvicorn worker. Neurocomment join cap counts NC joins only (not warming). Listener membership ceils ~500 channels/account (needs sharding beyond); SQLite single-writer is the eventual Postgres trigger.

## Routing
| Task | Load |
|---|---|
| Backend flow, stack, services, gateways | `context/architecture.md` |
| Backend coding or review | `context/conventions.md` |
| React, FSD, TypeScript, i18n | `context/frontend.md` |
| Telegram, proxy, warming, neurocomment | `context/runtime.md` |
| Setup, commands, CI reproduction | `context/setup.md` |
| Repeatable implementation | `patterns/INDEX.md`, then one matching pattern |
| Why or history | `mex timeline --kind decision --limit 3`, git, merged PRs |

Load only the matching route and at most one relevant pattern. Trust code, tests, manifests, and workflows over memory.

## Workflow
1. **CONTEXT** — load the matching context; check `patterns/INDEX.md` for a relevant runbook.
2. **BUILD** — follow the loaded rules and pattern; state any necessary deviation before implementing it.
3. **VERIFY** — run relevant checks from `context/setup.md`; report only commands actually executed and their results.
4. **DEBUG** — fix failures, use a matching debug pattern when available, and rerun failed verification.
5. **GROW** — update this State only when reality changed; update affected context facts; create or improve a repeatable pattern; bump `last_updated`; use `mex log` when rationale matters.
