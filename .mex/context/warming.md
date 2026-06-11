---
name: warming
description: Scheduled warming activity — APScheduler jobs that drive human-like behaviour on accounts. Load when adding, debugging, or tuning warming jobs.
triggers:
  - "warming"
  - "warm-up"
  - "scheduler"
  - "apscheduler"
  - "cron"
  - "job"
edges:
  - target: context/telegram.md
    condition: when the job's body performs Telegram I/O
  - target: context/architecture.md
    condition: when deciding where job code lives vs UI code
  - target: patterns/add-warming-job.md
    condition: when adding a new scheduled warming task
last_updated: 2026-06-12
---

# Warming (per-account asyncio loops)

## Runtime model (decided at first implementation)

Warming is a **continuous randomised loop per account**, not a fixed-schedule cron job:
`cycle → sleep 12-30h → cycle …`. So instead of APScheduler we run one
`asyncio.Task` per warming account, owned by `services/warming.py` in the module-level
`_RUNTIME: dict[str, asyncio.Task]`.

- `start_warming(account_id)` sets state `active` and creates the loop task.
- `stop_warming(account_id)` cancels the task and resets state to `idle`.
- The loop wrapper `_warming_loop` calls the testable `run_one_cycle`, then sleeps a
  random 12-30h, updating `warming_account_state` (next_run_at, last_cycle_at, cycle count).

APScheduler / `core/scheduler.py` is **only** needed if a future feature wants real cron
scheduling — warming does not. This resolves the old "shared scheduler handle" and
"jobstore" open decisions for the warming domain.

## Cycle design rules

- `run_one_cycle(WarmingCycleRequest)` is the pure, testable unit — it returns a
  `WarmingCycleResult` and performs no infinite sleeps (delays come from `settings.warming`,
  set to 0 in tests).
- All Telegram I/O goes through `core.telegram_client.execute(account_id, action)` with
  typed actions (`SetOnline`, `JoinChannel`, `ReadChannel`, `ReactToPost`, `SendDirectMessage`).
- A `flood_wait` result short-circuits the cycle; the loop then parks the account in the
  `flood_wait` state until the next scheduled cycle.
- The loop body never raises to the task: `_warming_loop` catches everything, logs
  `warming_loop_crashed` via `core/logging.py`, and sets state `error`.

## Persistence

- Channels: `warming_channels` (unlimited, deduped). Settings: `warming_settings`
  (singleton row — inter-account-chat toggle, reactions toggle, Gemini API key + model).
  Per-account runtime: `warming_account_state` (state, cycles_completed, last_event,
  last_cycle_at, next_run_at).
- The Gemini key is entered in the UI and stored in `warming_settings`; `settings.gemini`
  only supplies defaults (env `GEMINI__API_KEY`) and non-secret tunables.

## Human-like activity (decided)

All in `settings.warming`, no magic numbers in code:
- Per-action pause 10-30s; "typing" 5-30s; "reading posts" 8-45s.
- Random 1-3 channels per cycle (`channels_per_cycle_min/max`).
- Reaction probability per read (default 0.6) using a configurable emoji set; reactions
  target a random recent post.
- Post-cycle sleep 12-30h with per-account startup jitter.
- Inter-account chat (opt-in): when ≥2 accounts are warming and a Gemini key is set, the
  sender DMs a random warming peer (that has a known `user_id`) a Gemini-generated line.
- Per-account daily quotas are a future refinement.

## What does NOT belong here

- No direct Telegram calls inline — go through `core/telegram_client.py`.
- No business logic in `features/warming.py` — it is UI-thin and delegates to
  `services/warming.py`.
