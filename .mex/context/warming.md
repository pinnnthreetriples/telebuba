---
name: warming
description: Warming runtime invariants and ownership.
triggers: [warming, runtime, cycle, pacing, persona]
edges:
  - target: context/services.md
    condition: business logic
  - target: context/telegram.md
    condition: Telegram actions
  - target: patterns/add-warming-job.md
    condition: new warming step
last_updated: 2026-07-16
---

# Warming
Warming is one persisted `asyncio.Task` per active account. `services/warming/_runtime.py` owns start/stop/reconcile/shutdown; FastAPI lifespan restores tasks after restart.

## Flow
Readiness gate → schedule/pacing → `run_one_cycle` → typed Telegram actions → persist counters, progress, cooldown, and `next_run_at` → sleep.

## Ownership
- `board.py`, `channels.py`, `settings_store.py`: UI read model and configuration.
- `pacing.py`, `_fleet.py`: phase/persona limits, active-window scheduling, affinity, quiet days, fleet de-correlation.
- `_cycle.py`, `_chat.py`, `_stories.py`: one testable session.
- `_loop.py`, `_runner.py`, `_transitions.py`, `_runtime.py`: gates, persistence, sleeps, task ownership.

## Invariants
- Persona sets target cadence; phase/trust caps remain authoritative.
- Telegram I/O uses typed gateway actions; API/frontend contain no warming policy.
- Board data is bulk-loaded; do not add per-card queries.
- Loop failures are logged and persisted, never silently lose task ownership.
- Config fields are mirrored in `.env.example`.
- Known defect: a mid-cycle restart may undercount daily actions (`#208`).