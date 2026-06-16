---
name: warming
description: Warming runtime workflow — per-account asyncio tasks, cycle execution, persisted runtime state, board read model, and settings. Load when adding, debugging, or tuning this domain.
triggers:
  - "warming"
  - "warm-up"
  - "runtime"
  - "scheduler"
  - "asyncio"
  - "cycle"
  - "job"
edges:
  - target: context/telegram.md
    condition: when the cycle performs Telegram I/O
  - target: context/architecture.md
    condition: when deciding where runtime code lives vs UI code
  - target: context/services.md
    condition: when changing the warming service package
  - target: patterns/add-warming-job.md
    condition: when adding a new warming runtime task
last_updated: 2026-06-16
---

# Warming Runtime

## Runtime model

Warming is a continuous per-account runtime workflow, not a fixed cron job. The domain uses one `asyncio.Task` per active account, owned by `services/warming/_runtime.py` in `_RUNTIME`.

- `start_warming(account_id)` validates readiness, sets state, and creates the loop task.
- `stop_warming(account_id)` cancels the task and resets state.
- `shutdown_warming_runtime()` cancels running tasks on app shutdown.
- `reconcile_warming_runtime()` rebuilds the in-memory task set after app startup based on persisted state.
- `run_one_cycle(...)` is the testable cycle unit.
- `run_loop_iteration(...)` owns persisted next-run calculation and state updates.

APScheduler / `core/scheduler.py` is not used for the current warming runtime. Add a scheduler only if a future feature needs true cron semantics.

## Package layout

```text
services/warming/
├── __init__.py        public API re-exports
├── channels.py        channel input parsing/list/add/remove
├── settings_store.py  settings row load/save
├── board.py           kanban/read-model builder; bulk-loads signals
├── pacing.py          scheduling, readiness, intensity, time helpers
├── _seams.py          injectable execute/generate_text/status/rng seams
├── _state.py          state transition helpers
├── _chat.py           Gemini chat helper + text sanitisation
├── _cycle.py          one-cycle execution
├── _loop.py           one loop iteration + recovery paths
└── _runtime.py        task ownership, start/stop/reconcile/shutdown
```

`features/warming/` is UI-only: settings cards, channels UI, board rendering, activity log. It must delegate all domain logic to `services/warming/`.

## Cycle design rules

- `run_one_cycle(...)` is the unit-level business operation. It returns a Pydantic result and performs no infinite loop itself.
- Telegram I/O goes through `core.telegram_client.execute(account_id, action)` with typed actions.
- Rate-limit / failure statuses returned by the executor are surfaced in the cycle result and persisted by the loop/state layer.
- The loop body never lets exceptions kill task ownership silently: loop errors are logged and state is updated.
- `run_loop_iteration` is the single writer of next-run state; task wrappers sleep based on persisted state.
- Readiness and pacing policies live in `services/warming/pacing.py` and `settings.warming` / persisted warming settings.

## Persistence

- Channels: `warming_channels`.
- Settings: `warming_settings` singleton row — feature toggles, Gemini key/model, runtime controls.
- Per-account runtime state: `warming_account_state` — state, counters, last event/action/channel/error, heartbeat, started/stopped timestamps, next run, cooldown fields, proxy snapshot, daily counters, quarantine count.
- DB queries live in `core/repositories/warming.py` and are re-exported through `core/db.py` for compatibility.

## Board/read model

`services/warming/board.py` builds the board read model for the UI.

Important invariant: `load_board()` bulk-loads accounts, runtime states, channels, spam/status signals, fingerprints, and settings once. It must not reintroduce per-card DB queries.

## Settings

- Static defaults live in `core/config.py` under `settings.warming` and `settings.gemini`.
- User-editable controls live in the `warming_settings` table and are read/written through `services/warming/settings_store.py`.
- `.env.example` must include every config field; `tests/test_architecture.py` enforces this.

## What does NOT belong here

- No direct Telegram SDK calls inline — go through `core.telegram_client`.
- No business logic in `features/warming/` — it is UI-thin and delegates to `services/warming/`.
- No APScheduler assumptions for this domain.
- No `telegram_outbox` assumptions; the current model is direct executor + persisted runtime state.
