---
name: heartbeat
description: Lightweight MEX health and cleanup procedure.
last_updated: 2026-07-16
---

# Heartbeat

1. Run `npx mex-agent heartbeat` from the repository root.
2. On `HEARTBEAT_OK`, report exactly that.
3. For stale files, verify them against current code before changing `last_updated`, then run `mex sync` when needed.
4. Keep live state compact in `ROUTER.md`; keep history in git and `.mex/events/decisions.jsonl`.
5. Run `npx mex-agent doctor` after cleanup.
