---
last_updated: 2026-07-16
---

# Telebuba Router

## State
- Working: React/FastAPI; accounts/sessions/proxy pool/profile media/channels; warming runtime; neurocomment listener/vision solver; strict CI.
- Deferred: landing `#237`, worker/remote DB architecture, full operator/deployment docs.
- Known: warming daily cap may undercount after a mid-cycle restart (`#208`); use one uvicorn worker.

## Load
| Task | File |
|---|---|
| Backend flow, stack, services, gateways | `context/architecture.md` |
| Backend coding/review rules | `context/conventions.md` |
| React/FSD/i18n | `context/frontend.md` |
| Telegram, proxy, warming, neurocomment | `context/runtime.md` |
| Setup, commands, CI reproduction | `context/setup.md` |
| Repeatable implementation | `patterns/INDEX.md` |
| Why/history | `mex timeline --kind decision --limit 3`, git, merged PRs |

Load only one route plus one matching pattern when needed. Trust code/tests over memory. Update this state only when current reality changes.
