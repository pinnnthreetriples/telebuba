---
name: active-state
description: Live project state — what works, what is not yet built, known issues. Updated by the agent in the Record step of GROW after meaningful work.
last_updated: 2026-07-10
---

# Active State

This file is a **snapshot, not a changelog** — history lives in git / merged PRs / `.mex/events/`. Keep it short; compacted 2026-07-06.

## Current State

**Feature-complete and stable on `main`** (as of 2026-07-08, HEAD ≈ PR #204). The React SPA + FastAPI `/api/v1` split stack, the pixel-perfect design port, full backend wiring, warming personas, neurocomment Ф1+Ф2 with the vision captcha solver, the 2026-07-02 audit remediation (#197), nightly/CI hardening (#199), and the 2026-07-08 audit remediations — neurocomment solver/quota-race/locale-neutral onboarding (#201) and warming anti-ban fleet de-correlation (#202) — are all merged. CI (7 jobs) and the nightly (mutation ~72.7% kill / semgrep-full / hypothesis-extended) are green. Current work = the #203 warming fleet-correlation tuning (PR open, pending merge): per-account chronotype morning distribution (triangular fleet spread, stable base + daily jitter, clamped to the window), channel-affinity churn + off-affinity exploration + lower default ratio + per-deployment salt, cold-start spread across ~24h, and per-calendar-day weekend-biased quiet days — all in the new pure `services/warming/_fleet.py`. The 2026-07-10 targeted audit
refactor is also done (behaviour-preserving, all gates green): the two oversized
React components were decomposed into co-located `ui/` siblings —
`AccountEdit.tsx` (1274→~85 lines) into Session/Proxy/Device/Signals/Actions
sections (+`_shared.tsx`/`_styles.ts`), `NeurocommentPage.tsx` (1408→~570) into
Pipeline/ActivityLog/Idle/Listener/CaptchaSolver/Campaigns/HowItWorks cards
(+Odometer/SurfHover/CaptchaQueue); and the warming complexity cluster was eased
in-module (`start_warming` C17→A via `_carry_or_restamp`/`_cancel_existing_task`/
shared `_enforce_start_readiness`, `_run_chat_step` C12→B via `_should_chat`,
`_read_and_react`'s 5-tuple → `_ReadReactOutcome` dataclass). The third audit
item (telegram `_dispatch_action`/`_pick_reaction` tidy) was deliberately dropped
during execution — the extraction didn't clear the lint suppression and tripped
the file-size gate, i.e. net-negative churn on already-clear dispatch code.

## Not Yet Built (deliberate)

- **#149 HITL captcha canary** — operator-run; never an agent task.
- PM-delivered captchas (Rose non-button modes) not watched; grid/slider/jigsaw give up by design.
- Proxy unassign-from-account: backend exists (`POST /proxies/unassign`), no UI trigger.
- `ListenerEditModal` persists the picked listener only while the runtime runs.
- Accounts table status filter / column sort / bulk actions — backend supports, UI gap.
- Photo «сделать главной» — no set-existing-as-main RPC in the gateway.
- Warming per-action numeric limits stay auto/config (read-only in UI, auto-cap ADR).
- **Interest-partitioned channel catalog** — the durable fix for cross-account channel overlap (joins are permanent + the pool is shared, so churn/exploration only bound convergence, not eliminate it). Explicitly deferred by #203 as a separate follow-up.

## Known Issues

- aislop gate = `uv run python tools/aislop_gate.py`; `python -m aislop` does not work (npm tool).
- Local `prettier --check .` floods with CRLF false positives (LF in git, CI green) — check only changed files after `prettier --write`.

## Open Decisions

- **Account lifecycle enum beyond session health** — `accounts.status` (session health) vs `warming_account_state.state` (runtime lifecycle); a unified business lifecycle is undecided. (`context/telegram.md`)
- **Project purpose / "why"** — deliberately deferred; not documented anywhere.
