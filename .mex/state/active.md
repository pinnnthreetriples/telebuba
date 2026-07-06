---
name: active-state
description: Live project state — what works, what is not yet built, known issues. Updated by the agent in the Record step of GROW after meaningful work.
last_updated: 2026-07-06
---

# Active State

This file is the only place that should change after every meaningful task. `ROUTER.md` stays stable.
It is a **snapshot, not a changelog** — full per-PR history lives in git / merged PRs / `.mex/events/`.
Compacted 2026-07-06 (the old append-only changelog is in git history of this file).

## Current State

**The project is feature-complete and stable on `main`.** All planned epics are merged; CI (7 jobs) and the nightly (mutation / semgrep-full / hypothesis-extended) are green. Work now is bug fixes and small operator-driven improvements.

## Shipped (milestone summary, newest first)

- **Nightly repair + CI supply-chain hardening** — PR #199 (2026-07-06): mutmut `also_copy` += `api`/`main.py`; runner disk-free step for the ~7000-mutant sweep (~72.7% kill, survivors don't gate); all actions SHA-pinned; dependabot + uv 7-day cooldowns; semgrep `--exclude-rule` mutes removed (0 findings unmuted).
- **UI bug triage** — PR #198 (2026-07-03): dead-proxy warning, nav backdrop-blur ghosts, promoted-account graduation semantics («в прогретые» always lands in «Прогреты»), reaction whitelist fix for restrictive channels, 24h time, live pause countdown. Plus (same branch): **vision captcha solver** (Gemini/OpenAI, keys in DB via migration #26, solver ON by default, retry instead of give-up).
- **Full audit remediation + channel-pin & dialogue-feed features** — PR #197 (2026-07-03): all ~87 findings of the 2026-07-02 audit fixed (incl. the critical SPA path-traversal); TanStack Table/Form + Zod adopted as the FE foundation (shared `DataTable`/`FormField`).
- **Warming activity personas** — PR #193 (2026-07-01): per-account calm/normal/active cadence under the phase safety ceiling; session-based scheduling replaced flat `cycle_sleep_*`; quiet hours removed entirely (active hours is the single concept); story-view step + `WatchPeerStories` action.
- **Design polish + full backend wiring** — PRs #188–#192 (2026-06-30…07-01): pixel-perfect `Telebuba.dc.html` port (React+Tailwind, ~13 modals, CSS-only animations), then the make-it-real epic Phases 0–7 (proxy pool migration #18, trust/device de-mock, real account checks, phone-code login/logout/reset, real logs/captcha-queue/board, neuro settings store + engine `min_trust_score`, profile media) and the finish-design↔backend pass — every visible control drives `/api/v1`.
- **Split-stack migration #163–#174** — PRs #176–#186 (2026-06-28): NiceGUI deleted; FastAPI `api/` (UI-thin, error envelope, `Page[T]`, JWT cookie auth, SSE `/events`) + React+TS+Vite FSD `frontend/` with generated hey-api client and a CI drift gate.
- **Neurocomment Ф1+Ф2** — merged 2026-06-23/24 (PRs #122–#128, #150–#154): campaign comment automation + challenge solver.
- **Warming M1–M3** (#34–#45) + strict quality gates + repository split — the 2026-06 foundation.

## Not Yet Built (deliberate)

- **#149 HITL captcha canary** — operator-run live canary; deliberately NOT an agent task.
- PM-delivered captchas (Rose non-button modes) — detector watches only the group; grid/slider/jigsaw types intentionally give up.
- Proxy unassign-from-account has a backend (`POST /proxies/unassign`) but no UI trigger.
- `ListenerEditModal` persists the picked listener only while the runtime runs (stopped-edit needs a thin endpoint around `set_listener_account_id`).
- Accounts table status filter / column sort / bulk actions — feature gap, backend supports it.
- Photo «сделать главной» — Telegram has no set-existing-as-main RPC in the gateway.
- Warming per-action numeric limits stay auto/config (read-only in UI, per the auto-cap ADR).

## Known Issues

- The aislop gate is `uv run python tools/aislop_gate.py` (npx-based). `python -m aislop` does NOT work — aislop is an npm tool, not a Python module.
- Local `prettier --check .` floods with CRLF false positives on this Windows checkout (LF in git, CI green) — check only changed files after `prettier --write`.

## Open Decisions

Authoritative list of architectural unknowns. Context files may carry `[TO BE DETERMINED]` markers; this section is the single index of all of them.

- **Account lifecycle enum beyond session health** — session health is stored on `accounts.status`; warming/runtime lifecycle lives separately in `warming_account_state.state`. A unified business lifecycle is still undecided. (`context/telegram.md`)
- **Project purpose / "why"** — deliberately deferred; not documented anywhere.
