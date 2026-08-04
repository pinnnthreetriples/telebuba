---
name: agents
description: Always-loaded project anchor with identity, hard rules, commands, and navigation.
last_updated: 2026-08-04
---

# Telebuba

## What This Is
Telegram operations dashboard for accounts, proxies, warming, neurocomment, profiles, and channels.

## Non-Negotiables
- Preserve `api → services → core` and typed Pydantic boundaries.
- Keep external I/O in `core/`; API and frontend contain no runtime policy.
- Never expose secrets, sessions, tdata, JWTs, or proxy credentials.
- Add tests for behavior changes; test files stay at or below 700 lines.
- Run one uvicorn worker; report only checks actually executed.

## Commands
- Dev: `uv run uvicorn main:app --reload`; frontend: `cd frontend && npm run dev`
- Backend: `uv run pytest`
- Quality: `uv run pre-commit run --all-files`
- Pre-push gates, which no `pre-commit run --all-files` or `--files` invokes: `uv run pre-commit run --hook-stage pre-push aislop --all-files`, same for `arch-guard`. Required CI enforces both (`aislop` job, `test` job), so a green `--all-files` proves nothing about them.
- Frontend: `cd frontend && npm run gates && npm run build`
- Memory: `npx mex-agent check --quiet`

On a Windows checkout (`core.autocrlf=true`) the two repo-wide steps trip on
pre-existing CRLF files, not on your change: `npm run gates` fails at its
`format` step and `pre-commit run --all-files` rewrites every such file. Run
`prettier --write` on the files you touched, the other frontend gates
individually, and check scope with `git diff HEAD --name-only`, not `git status`.

## GROW
After meaningful work:
- update `.mex/ROUTER.md` only when project state changes;
- update affected `.mex/context/` facts;
- create or improve a `.mex/patterns/` runbook for repeatable work;
- bump `last_updated` and use `mex log` when rationale matters.

## Navigation
Read `.mex/ROUTER.md`, then load only its matching task route and at most one relevant pattern.
