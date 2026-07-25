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
last_updated: 2026-07-26
---

# Telebuba Router

## State
- Working: React/FastAPI; accounts, sessions, proxy pool, profile media, profile privacy keys (photo / bio / last seen, per account or fleet-wide), channels, warming runtime, neurocomment listener and vision solver, automated campaign channel discovery (Telegram native search + Telemetr.io), strict CI, incl. CVE checks on both dependency trees (`pip-audit`, `npm-audit`, plus a nightly repeat).
- Deferred: landing #237, worker/remote DB architecture, full operator and deployment documentation, persistent neurocomment post queue + catch-up, send↔DB idempotency reconciliation, backup readiness/off-site.
- Known: `main` requires all eight `ci.yml` checks to pass before merging (since 2026-07-25 — they were advisory before, and a red PR was mergeable). `mex.yml` is path-filtered and deliberately not required. Admins can still bypass, so a deliberate override stays possible; `ci.yml` must not regain `paths-ignore` or docs-only PRs will hang on a check that never reports. Warming daily cap may undercount after a mid-cycle restart (#208); use one uvicorn worker. Neurocomment join cap counts NC joins only (not warming). Listener membership ceils ~500 channels/account (needs sharding beyond); SQLite single-writer is the eventual Postgres trigger. Channel discovery is operator-triggered only (no scheduler); it uses ONE account (listener, else the campaign's first) and refuses to start on a cooling account. On a Windows checkout (`core.autocrlf=true`) `npm run format` flags CRLF files the SPA's `endOfLine: "lf"` rejects; run `prettier --write` on changed files, not a repo-wide check. Same trap for `pre-commit run --all-files`: the `mixed-line-ending --fix=lf` hook rewrites every CRLF file, so `git status` reports hundreds of modified paths whose blob hash is unchanged (`git diff` shows the real set) — harmless, but verify scope with `git diff HEAD --name-only`, not `git status`.

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
