---
name: add-warming-job
description: Add or change warming runtime work in the per-account asyncio-task model.
triggers:
  - "warming job"
  - "scheduled job"
  - "warming runtime"
  - "asyncio task"
  - "cycle"
  - "runtime"
edges:
  - target: context/warming.md
    condition: always — defines runtime ownership and cycle rules
  - target: patterns/add-telegram-task.md
    condition: when the runtime body performs Telegram I/O
  - target: context/conventions.md
    condition: when shaping schemas and logging
last_updated: 2026-06-16
---

# Add Warming Runtime Work

## Context

Read `context/warming.md`. Hard rules:
- Warming does **not** use APScheduler.
- Runtime ownership lives in `services/warming/_runtime.py` as per-account `asyncio.Task`s.
- Cycle logic lives in `services/warming/_cycle.py` / `_loop.py`; UI lives in `features/warming/` and must stay thin.
- Runtime state is persisted in `warming_account_state` through repository helpers.

## Steps

1. **Define/extend schemas.** Add request/result/state fields in `schemas/warming.py` when crossing layer boundaries.
2. **Place the logic in the right submodule:**
   - channel parsing/listing → `services/warming/channels.py`
   - settings row → `services/warming/settings_store.py`
   - board read model → `services/warming/board.py`
   - timing/readiness/intensity helpers → `services/warming/pacing.py`
   - one-cycle behavior → `services/warming/_cycle.py`
   - loop/recovery/next-run state → `services/warming/_loop.py`
   - task ownership/start/stop/reconcile → `services/warming/_runtime.py`
3. **Persist state deliberately.** If the runtime decision changes account state, write it through `core/repositories/warming.py` / `core.db` re-export.
4. **Catch and classify failures.** Runtime loops must log and persist errors; they should not silently die.
5. **Test.** Cover happy path, persisted state update, and one failure path. Patch seams in `services.warming._seams` or the owning submodule.
6. **Run gates.** `uv run pytest` and relevant lint/type gates.

## Gotchas

- Do not create a new scheduler for warming. The current model is service-owned async tasks.
- Do not put runtime logic in `features/warming/`; UI should only call services and render.
- Do not reintroduce per-card DB queries in the board. `load_board()` bulk-loads signals once.
- Do not sleep directly inside a unit-level cycle test path; keep sleep/timing injectable or configurable.
- Do not let a task cancellation hang forever; stop paths should cancel and await with a timeout.

## Verify

- [ ] Logic is in the correct `services/warming/` submodule
- [ ] No APScheduler dependency or assumptions
- [ ] Runtime state is persisted where needed
- [ ] Failure path logs and updates state
- [ ] No `features/*` import from another feature domain
- [ ] Tests cover happy path, persisted-state behavior, and failure path
- [ ] `uv run pytest` passes

## Debug

- Runtime does not resume after restart → check `reconcile_warming_runtime()` and stored state.
- Task appears stuck after stop → check cancellation/timeout handling.
- Board is slow → check that a change did not reintroduce per-card DB calls.
- State flips unexpectedly → check which function owns the write; `run_loop_iteration` should own next-run state.

## After

Run the GROW step from `ROUTER.md` (update `state/active.md`, touch any out-of-date `context/`, bump `last_updated`).
