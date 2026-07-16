---
name: add-warming-job
description: Add or change work in the per-account warming runtime.
triggers: [warming job, warming runtime, cycle, asyncio task]
edges:
  - target: context/warming.md
    condition: runtime invariants
  - target: patterns/add-telegram-task.md
    condition: Telegram action
last_updated: 2026-07-16
---

# Change Warming Runtime

## Steps
1. Update `schemas/warming.py` for boundary changes.
2. Put logic in the owning module: board/settings/channels, pacing/fleet, cycle steps, transitions/loop/runner, or runtime ownership.
3. Persist state through the warming repository; keep sleeps/timing injectable.
4. Classify/log failures so tasks cannot die silently.
5. Test normal behavior, persistence/restart behavior, and a failure/cancellation path.
6. Run relevant backend gates.

## Verify
No scheduler or UI/API business logic; board remains bulk-loaded; Telegram uses typed actions; stop/reconcile paths are bounded; `#208` is considered when changing daily counters.