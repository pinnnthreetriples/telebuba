---
name: agents
description: Smallest always-loaded Telebuba anchor: hard rules and where to read next.
last_updated: 2026-10-10
---

# Telebuba

## Hard rules
- `api → services → core`; external I/O only in `core/`. Read `CODING_STANDARDS.md` before writing code.
- Never print, log, commit or paste secrets, sessions, tdata, JWTs or proxy credentials.
- Behavior changes ship with tests. Run one uvicorn worker.
- Report only checks you actually ran.

## Read next
- Commands, CI and hooks: `.mex/context/setup.md`.
- Domain work: `.mex/ROUTER.md`, then one matching context and at most one pattern. Finish with `npx --yes mex-agent@0.7.1 check --quiet`.
