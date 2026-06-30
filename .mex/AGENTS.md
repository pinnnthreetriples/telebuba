---
name: agents
description: Always-loaded project anchor. Read first. Project identity, stack, file map, non-negotiables, commands, pointer to ROUTER.md.
last_updated: 2026-06-28
---

# Telebuba

## What This Is
Telegram account operations dashboard: account/session management, proxy/profile metadata, runtime workflows, logs, and AI-assisted text generation through typed gateways.

## Stack
**Backend:** Python 3.13 · FastAPI + uvicorn (single-worker) · SQLAlchemy/SQLite · Telethon · httpx · loguru+Sentry · pydantic-settings · uv · ruff · ty · pytest · hypothesis · bandit · pip-audit · semgrep · deptry · vulture · radon · aislop · pre-commit
**Frontend (`frontend/`):** React + TypeScript (strict) · Vite · TanStack Router/Query/Table/Form · Tailwind + shadcn/ui · `@hey-api/openapi-ts` · react-i18next · Sentry React · Vitest + Playwright · Steiger boundary-lint (full law in `context/frontend.md`)

## File Map
```text
telebuba/
├── main.py                 FastAPI/uvicorn composition root: lifespan (start/stop warming+neurocomment runtimes) + /api/v1 routers + StaticFiles mount of frontend/dist (catch-all → index.html); single-worker
├── pyproject.toml          uv project + strict test/lint/security gates
├── .env                    local secrets — gitignored
├── .env.example            committed template; must mirror core/config.py
├── api/                    UI-thin top layer (replaces features/): /api/v1 routes — validate → call service → serialize; imports only services/schemas/core.config/core.logging/fastapi
│   ├── v1/                 versioned routers, one per domain (accounts/proxies/warming/neurocomment/logs/settings/auth) + events.py (SSE live-event stream, text/event-stream; hidden from OpenAPI)
│   ├── deps.py             shared dependencies (Depends(get_current_user), pagination params)
│   └── errors.py           error-envelope mapping ({error:{code,message,fields?}}, 422 remapped)
├── core/                   infrastructure gateways; only layer touching third-party SDKs
│   ├── db.py               shared SQLite plumbing + compatibility re-exports
│   ├── migrations.py       versioned append-only migration registry + runner; apply_migrations() runs on engine init
│   ├── migration_steps.py  migration step bodies (split from migrations.py for the file-size budget)
│   ├── migration_steps_pool.py  proxy-pool migration (#18) body — split from migration_steps.py for the size budget
│   ├── device_fingerprint.py  generates/reads immutable per-account device profile
│   ├── phone_geo.py        phone number → geo lookup helper
│   ├── proxy_check.py      connectivity check for proxy configs
│   ├── tdata_import.py     converts tdata.zip to Telethon .session files (safe-extract)
│   ├── repositories/       per-aggregate DB query modules
│   │   ├── proxies.py         proxy-pool data layer (shared proxies + accounts.proxy_id assignment, capacity, connectivity-check persistence)
│   │   ├── warming_joined.py  tracks channels an account already joined (join-dedup)
│   │   └── neurocomment/      neurocomment data layer (campaigns, channel/account links, linked-group cache, readiness, comment claims, comment quota counts in _quota.py, challenge audit+cache in _challenges.py)
│   ├── telegram_client/    Telethon gateway package; public API re-exported from core.telegram_client
│   │   ├── _pool.py           client pool management
│   │   ├── _read.py           message reading actions (incl. CheckMessagesAlive deletion probe)
│   │   ├── _read_stories.py   story reading actions
│   │   ├── _read_challenge.py WaitForBotChallenge match predicate + NewMessage wait shell (neurocomment solver)
│   │   ├── _listener.py       standing post listener (subscribe_posts/stop_post_listener) for neurocomment
│   │   └── _video.py          video/media actions
│   ├── config.py           pydantic-settings, nested namespaces (incl. settings.api, settings.auth)
│   ├── gemini.py           HTTP gateway for Gemini
│   ├── events.py           in-process pub/sub for live log events (SSE backbone); core/logging publishes each persisted row here
│   ├── auth.py             password hashing + JWT encode/decode (only place tokens are minted/verified)
│   └── logging.py          loguru + SQLite logs + optional Sentry
├── schemas/                Pydantic models; shared types, no behavior, no I/O (incl. api.py: error envelope + generic Page[T]; challenge.py: bot-challenge message + audit-row models)
├── services/               business logic; UI-agnostic; no SDK imports
│   ├── accounts/           account/session/profile operations
│   ├── proxies.py          proxy-pool business logic (add/list/assign/unassign/remove/check over the pool repo)
│   ├── warming/            runtime workflow domain package (board.py also exposes list_warmed_accounts for the neurocomment overview)
│   ├── neurocomment/       campaign comment automation: campaigns.py (page→repo setup seam: create/list/link/assign; link_channel returns a typed outcome), onboarding.py (pre-join+readiness + one-shot spam probe), engine.py (on-post pipeline handle_new_post; bulk in-memory account selection, cached spam), _runtime.py (listener wiring + per-post task ownership + periodic deletion sweep + start/stop/reconcile-on-startup entrypoints + neurocomment_runtime_status read model for the UI running indicator), board.py (work-view read model, bulk-loaded; bot_challenge derived from the challenge audit table), challenge.py (proactive challenge solver — WaitForBotChallenge → cache/Gemini decision → click; audit row), _filters.py (pure post-filter: which posts to comment on), _state.py (transient per-account cooldowns + escalating channel deletion & challenge back-off), _seams.py (execute/generate_text/refresh_spam_status/rng)
│   ├── content.py          content generation orchestration
│   ├── dialogues.py        dialogue partner matching + pair assignment (DialoguePartnersResult/DialoguePairsResult)
│   ├── logs.py             log query helpers for the Logs page
│   ├── events.py           live-event seam: re-exports core.events.subscribe for the api/ SSE endpoint (api → services → core)
│   ├── spam_status.py      account spam/ban signal helpers
│   ├── auth/               auth policy (verify credentials, issue/slide session) over core/auth.py + users repo
│   └── trust.py            trust-score calculation from stored signals
├── frontend/               React + TS (strict) + Vite SPA — Feature-Sliced Design (app/routes/pages/widgets/features/entities/shared); the only UI; full law in context/frontend.md
│   ├── src/                FSD layers; shared/api holds the generated hey-api client + TanStack Query
│   ├── tailwind.config.*   design tokens (single source of truth)
│   └── package.json        FE deps + gate scripts (eslint/prettier/boundaries/tsc/vitest/playwright/gen-api)
└── tests/                  mirrors source tree; includes architecture/property tests
```

For the live implementation state, read `state/active.md`. This anchor is only the stable
routing summary describing the **split-stack target** (the 3 ADRs of 2026-06-28); `api/` and
`frontend/` are built out across slices #164–#174.

## Non-Negotiables (one-line each — full text in `context/conventions.md`; frontend law in `context/frontend.md`)
1. **API Layer is UI-thin (`api/`)** — `/api/v1` routes only validate → call a service → serialize; no business logic, no DB/Telegram access. `api/` imports **only** `services`/`schemas`/`core.config`/`core.logging`/`fastapi`.
2. **Pydantic Boundaries** — all inter-layer data through Pydantic models in `schemas/`; no raw `dict`/`tuple`/`list`; public funcs return models or `None` (lists via `Page[T]`).
3. **No Hardcoded Values (backend)** — tunables in `core/config.py`, secrets in `.env` via `core/config.py`. Frontend config via Vite `VITE_*`.
4. **Logging Only** — no `print()`; all logging via `core/logging.py`.
5. **Layer Isolation** — `api/` → `services`/`schemas`/`core.config`/`core.logging`/`fastapi`; `services/` → other `services/` + `core/` + `schemas/`; `core/` → `schemas/` + third-party; `schemas/` → `pydantic` + typing/stdlib only. Frontend is a separate tree reaching the backend only over `/api/v1`. Matrix in `context/architecture.md`.
6. **Gateways** — DB only via `core/db.py` and `core/repositories/`; Telegram only via `core.telegram_client.execute(account_id, action)` with typed action schemas. `sqlalchemy` / `telethon` forbidden in `services/` and `api/`.
7. **Test Coverage (strict)** — every endpoint/service change ships tests; warnings → errors; backend branch coverage ≥ 90%; frontend Vitest ≥ 80% + Playwright smoke; prefer `/tdd` skill.
8. **Async + Type Safety** — type hints everywhere; `from __future__ import annotations`; I/O is `async def`; `raise X(...) from e`.
9. **Device Fingerprint Immutable** — one profile per account, created at registration, never mutated.
10. **Configuration-Driven (backend)** — all limits/delays/proxies through `core/config.py`; nested namespaces (`settings.warming`, `settings.gemini`, `settings.api`, `settings.auth`, ...); no magic numbers.
11. **Services Layer** — all business logic lives in `services/<domain>/` or `services/<domain>.py`. `api/` routes validate, call services, serialize.
12. **Locale-neutral API** — responses carry codes/enums + ISO-8601 timestamps, never pre-translated text; the SPA owns all i18n.

Before adding files, follow `.mex/context/conventions.md` → **File Placement Guide** (where each kind of code goes, when to split, the package-root rule).

## Commands
- Install (backend): `uv sync`
- Dev (backend API): `uv run uvicorn main:app --reload` (single-worker)
- Test (backend): `uv run pytest`
- Lint / format: `uv run ruff check .` / `uv run ruff format .`
- Types: `uv run ty check .`
- Pre-commit: `uv run pre-commit run --all-files`
- Aislop on Windows: `uv run python -m aislop` if direct CLI invocation fails
- Regenerate the API client: `uv run python -m tools.gen_api` (dumps OpenAPI → hey-api → prettier; CI drift-checks it)
- Frontend (from `frontend/`): `npm install`; `npm run dev` (vite + `/api` proxy); `npm run gates` (eslint/prettier/boundaries/tsc/vitest); `npm run e2e` (Playwright)
- Full toolchain — `context/setup.md`.

## Scaffold Growth
After meaningful work, run GROW (full text in `ROUTER.md` Behavioural Contract):
- **Ground / Record / Orient / Write / Board.** Live state lives in `state/active.md`; board moves per `context/kanban.md`.

### Memory hygiene
1. **GROW before every PR** — update `state/active.md` and bump `last_updated` *before* opening the PR, not after merge.
2. **New module = new File Map line** — adding any new Python module under `api/`, `core/`, `services/`, or their subpackages means adding it to the File Map above in the same change.
3. **`.serena/` is deprecated** — do not use it; `.mex/` is the single source of truth. If Serena regenerates files there, delete them or add a deprecation header.
4. **One skill, one source** — edit `.claude/skills/` first, then sync to `.agents/skills/`.

## Skills
Project-local skills in `.claude/skills/` (matt-pocock). Full triggers in `context/skills.md`.

- `/tdd` — red-green-refactor; **mandatory** for any new feature or reproducible bug fix (non-negotiable #7).
- `/diagnose` — reproduce → hypothesise → instrument → fix; use when something is broken or throwing.
- `/prototype` — throwaway exploration before committing to a data model or UI; lives outside production tree.
- `/improve-codebase-architecture` — find deepening opportunities; run before a refactor.
- `/grill-with-docs` — stress-test a plan against `.mex/` context; use before any cross-layer change.
- `/zoom-out` — orientation map for an unfamiliar area; use before acting in unseen code.
- `/to-prd` — turn a conversation into a PRD on the issue tracker.
- `/to-issues` — split a plan into independently-grabbable board items → `Backlog`.

## Session Start
At the start of every new coding session, run from project root:

```
npx mex-agent check --quiet
```

If drift errors are reported, run before coding:

```
npx mex-agent sync --dry-run
```

Fix the flagged `.mex/` files, then proceed. For a full codebase brief (first session or after major changes):

```
npx mex-agent init
```

## Navigation
Read `ROUTER.md` at session start before any task. ROUTER drives every other context file from the routing table. Shell-command policy → `context/rtk.md`. Skills → `context/skills.md`. CI policy → `context/ci.md`.
