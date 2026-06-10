---
name: agents
description: Always-loaded project anchor. Read this first. Project identity, stack, file map, non-negotiables, commands, and pointer to ROUTER.md.
last_updated: 2026-06-10
---

# Telebuba

## What This Is
Telegram account farm manager: creates accounts, warms them up with human-like activity, and generates AI comments.

## Stack
Python 3.13 · NiceGUI · SQLAlchemy/SQLite · Telethon · APScheduler · httpx · loguru+structlog+Sentry · uv · ruff · ty · pytest

## File Map
```
telebuba/
├── main.py                 NiceGUI entrypoint (UI + scheduler)
├── pyproject.toml          uv project + locked deps
├── .env                    secrets (gitignored)
├── core/                   shared infrastructure — the only layer touching third-party SDKs
│   ├── config.py             pydantic + python-dotenv; single source of truth
│   ├── db.py                 SQLAlchemy gateway (only place sqlalchemy is imported)
│   ├── telegram_client.py    Telethon gateway (only place telethon is imported)
│   └── logging.py            loguru + structlog + Sentry (only place these are imported)
├── schemas/                Pydantic models; shared types, no behavior
├── features/               one file per user-facing feature; never imports another feature
└── tests/                  mirrors source tree
```

## Non-Negotiables (one line each — full text in `context/conventions.md`)
1. Feature isolation — one feature per file in `features/`; never modify existing feature files.
2. No cross-feature imports — `features/a.py` must not import `features/b.py`.
3. Pydantic at every layer boundary — no raw `dict`/`tuple`/`list` crossing layers.
4. No hardcoded values — tunables in `core/config.py`, secrets in `.env`.
5. No `print()` — `core/logging.py` only.
6. Layer isolation — see import matrix in `context/architecture.md`.
7. Gateways — DB only via `core/db.py`; Telegram only via `core/telegram_client.py`.
8. Test coverage — every new feature ships with a `tests/test_*.py`.
9. Async + type safety — type hints on every function; `from __future__ import annotations`; I/O is `async def`; `raise X(...) from e`.
10. Device fingerprint immutable — one profile per account, created once, never mutated.
11. Configuration-driven — no magic numbers; all tunables in `core/config.py`.

## Commands
- Install: `uv sync`
- Dev: `uv run python main.py`
- Test: `uv run pytest`
- Lint / format: `uv run ruff check .` / `uv run ruff format .`
- Types: `uv run ty check .`
- Pre-commit (all hooks): `uv run pre-commit run --all-files`
- Full toolchain — see `context/setup.md`.

## Scaffold Growth
After meaningful work, run GROW:
- **Ground:** what changed in reality?
- **Record:** update `ROUTER.md`'s pointer to `state/active.md`; update relevant `context/` files.
- **Orient:** create or update a `patterns/` runbook if this can recur.
- **Write:** bump `last_updated` on changed files; `mex log` when rationale matters.

## Default Skills (apply silently — do not announce or re-invoke each turn)

These are global skills the user has set as defaults. Every session applies them without being asked.

- **`karpathy-guidelines`** — coding behaviour: surface assumptions, prefer the minimum code, surgical changes only, transform tasks into verifiable goals. Applies to every write/review/refactor action.
- **`caveman`** — communication style: drop filler, articles, pleasantries; keep code, commands, errors, technical terms exact. Default to brief. Expand only when clarity, safety, or the user explicitly asks for detail.

## Agent Skills (project-local — invoke on the listed trigger)

Installed in `.claude/skills/`. The agent does NOT auto-run them — invoke via the `Skill` tool when the trigger fires. Prefer the skill over reinventing the workflow.

### Engineering loops

- **`/tdd`** — red-green-refactor. **Trigger:** any new feature in `features/` (rule 7 already mandates a test); any bug fix where reproducing in a test is feasible; user says "let's TDD this" / "test-first". Aligns with our strict pytest config and 90 % branch-coverage floor.
- **`/diagnose`** — disciplined bug loop: reproduce → minimise → hypothesise → instrument → fix → regression-test. **Trigger:** user reports something broken / throwing / failing; an integration regresses; a Sentry alert lands. Always prefer this over freelancing a fix.
- **`/prototype`** — throwaway exploration. **Trigger:** before committing to a non-obvious data model, state machine, or UI option. Forbidden in `features/` / `core/` / `schemas/` — prototypes live outside the production tree.
- **`/improve-codebase-architecture`** — find deepening opportunities. **Trigger:** when `core/` or `features/` start showing duplication, shallow modules, or the non-negotiables in `context/conventions.md` are getting bent. Run it before a refactor, not after.

### Planning and scoping

- **`/grill-with-docs`** — stress-test a plan against `.mex/` context and decisions. **Trigger:** before a non-trivial change, especially anything that crosses layer boundaries or touches a non-negotiable. Catches drift early.
- **`/zoom-out`** — higher-level map of an unfamiliar area. **Trigger:** when the agent is about to act in a part of the codebase it has not loaded into context this session.

### Board / issue tracker

- **`/to-prd`** — turn the current conversation into a PRD on the issue tracker. **Trigger:** user says "this should be a PRD" or after a long discussion that decided non-trivial scope.
- **`/to-issues`** — split a plan / PRD into independently-grabbable board items (tracer-bullet vertical slices). **Trigger:** after a PRD lands, or whenever a single conversation has grown several actionable threads. Outputs go to `Backlog` per `context/kanban.md`.

### Setup / safety

- **`/setup-matt-pocock-skills`** — bootstraps the `Project Skill Configuration` block below so `to-prd` / `to-issues` / `diagnose` / `tdd` / etc. know our issue tracker, triage labels, and domain layout. **Trigger:** if any matt-pocock skill complains it can't find that context. Currently the block is filled by hand, so re-run only after a major structural change.
- **`/git-guardrails-claude-code`** — install Claude Code hooks that block destructive git ops (`push --force`, `reset --hard`, `clean -fd`, `branch -D`). **Trigger:** if anyone in the session ever runs a destructive git command "by accident". Note: modifies global `~/.claude/settings.json`. Discuss with the user before invoking.

## Project Skill Configuration

This block is what `setup-matt-pocock-skills` would produce. Hand-written so the engineering skills above have the context they need.

- **Issue tracker:** GitHub Issues on `pinnnthreetriples/telebuba`. Use `gh` from `D:/gh.exe`.
- **Project board:** GitHub Project #2 (`telebuba`) — full protocol and column IDs in `context/kanban.md`. Status: `Backlog` → `Ready` → `In progress` → `In review` → `Done`.
- **Triage labels:** none yet. When the first label is created, add it here and to `state/active.md`.
- **Domain docs:** `.mex/ROUTER.md` is the entrypoint; `.mex/context/` and `.mex/patterns/` are the domain source of truth. `state/active.md` carries live state.

## Navigation
Read `ROUTER.md` at session start before any task. Live project state lives in `state/active.md`. Work is picked from the GitHub Project board — protocol in `context/kanban.md` (load this every session too).
