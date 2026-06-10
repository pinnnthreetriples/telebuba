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
last_updated: 2026-06-10
---

# Warming (APScheduler)

## Where the scheduler lives

- A single `AsyncIOScheduler` instance is created once at app startup and shared with NiceGUI's event loop. Owner module: `features/warming.py`.
- Other features must NOT spin up their own scheduler. If a feature needs scheduled work, it registers a job on the shared scheduler at startup or through a `core/` helper — without importing `features/warming.py`.
- [TO BE DETERMINED — populate after first implementation: where the shared scheduler handle is exposed so other features can register without crossing the no-cross-feature-import rule. Likely `core/scheduler.py`.]

## Job design rules

- Jobs are async functions that take a Pydantic model (typically an account identifier or job spec schema) and return a result model.
- Jobs are idempotent — APScheduler may fire late or twice; check state before acting.
- Jobs must not raise to the scheduler. Catch, log a business event via `core/logging.py`, and let the scheduler reschedule cleanly.
- Long-running jobs must `await` cooperatively so they do not block the NiceGUI event loop.

## Persistence

- Job definitions live in code; runtime job state (next run, last result) lives in SQLite via SQLAlchemy.
- [TO BE DETERMINED — populate after first implementation: whether APScheduler's own jobstore points at the same SQLite DB or stays in-memory with our DB tracking schedule state.]

## Human-like activity

[TO BE DETERMINED — populate after first implementation. Decide jitter strategy (random delays around scheduled time), per-account daily quotas, and which activities count as warming vs active use.]

## What does NOT belong here

- No direct Telegram calls inline in this file — go through `core/telegram_client.py`.
- No scheduling logic spread across multiple feature files — registrations live here (or via `core/scheduler.py` once it exists).
