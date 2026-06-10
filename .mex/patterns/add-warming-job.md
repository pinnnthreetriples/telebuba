---
name: add-warming-job
description: Register a new scheduled warming job on the shared APScheduler instance.
triggers:
  - "warming job"
  - "scheduled job"
  - "apscheduler"
  - "add_job"
  - "cron"
edges:
  - target: context/warming.md
    condition: always — defines scheduler ownership and job rules
  - target: patterns/add-telegram-task.md
    condition: when the job body performs Telegram I/O
  - target: context/conventions.md
    condition: when shaping the job's schemas and logging
last_updated: 2026-06-10
---

# Add a Warming Job

## Context

Read `context/warming.md`. Hard rules:
- A single shared `AsyncIOScheduler` lives in `features/warming.py` (or `core/scheduler.py` once it exists).
- Other features must not create their own scheduler.
- Jobs are async, idempotent, and never raise to the scheduler.

## Steps

1. **Define schemas.** A job-spec model (account id, params) and a result model. Both in `schemas/`.
2. **Write the job body.** An async function taking the spec model, returning the result model. If it does Telegram I/O, follow `patterns/add-telegram-task.md`.
3. **Make it idempotent.** Read current state from the DB before acting. If the work is already done (or out-of-window), return a no-op result.
4. **Register the job** on the shared scheduler. Trigger: cron or interval, with jitter so accounts don't all fire at the same second. [VERIFY AFTER FIRST IMPLEMENTATION — exact registration API once `features/warming.py` exists.]
5. **Catch everything.** Wrap the job body in `try/except`. Log a business event via `core/logging.py` on both success and failure. Never let an exception escape.
6. **Test.** Cover: happy path, idempotent re-fire, and one underlying failure (e.g. mocked `FloodWaitError`).

## Gotchas

- Scheduler fires can overlap with previous runs if the previous run hasn't finished. Use `max_instances=1` per job, or check inside.
- A raised exception inside a job can wedge APScheduler's logging — always catch.
- Without jitter, many accounts firing the same activity at `:00` looks robotic to Telegram. Add per-account random delay.
- Don't import `features/warming.py` from another feature file (no-cross-feature-import rule). Register through the `core/` helper once it exists.

## Verify

- [ ] Job is registered on the shared scheduler, not a new one
- [ ] Job body is async, takes and returns Pydantic models
- [ ] Job is idempotent — re-firing produces a logged no-op, not a duplicate action
- [ ] Job catches all exceptions and logs them via `core/logging.py`
- [ ] No `features/*` import from another `features/*`
- [ ] Test covers happy path, re-fire, and a failure path; `uv run pytest` passes

## Debug

- Job never fires → check scheduler is started and the trigger expression is right; check logs for registration errors at startup.
- Job fires twice and double-acts → idempotency check is missing or wrong; add a guard at the top of the body.
- Scheduler dies silently after an error → the job raised; wrap in try/except.

## After
Run the GROW step from `ROUTER.md` (update `state/active.md`, touch any out-of-date `context/`, bump `last_updated`).
