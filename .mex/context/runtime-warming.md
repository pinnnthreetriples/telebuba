---
last_updated: 2026-08-06
---

# Warming Runtime

- One in-memory `asyncio.Task` runs per persisted active account. FastAPI lifespan reconciles persisted state; one uvicorn worker is required while ownership is process-local.
- Persona controls cadence while phase/trust/readiness remain safety ceilings. Timing and caps come from config; persisted `next_run_at` survives restart, while an explicit stop/start may re-roll startup timing.
- Daily action budget is reserved before a cycle and reconciled afterward. The reservation is guarded by a per-booking token, not only generation or booked value, so a cancelled old cycle cannot release a newer booking. Hard process death remains fail-closed because actual spend is unknown.
- Cycle work spends one shared budget across online/join/read/react/story/DM actions. When tuning caps or cadence, verify later steps remain reachable rather than reasoning from a single action in isolation.
- Inter-account DMs resolve cold peers by phone through `contacts.resolvePhone`, never by saving them as contacts. A permanently unaddressable peer skips the turn without parking the healthy sender, but the attempt still consumes budget.
- Quarantine releases only on a confirmed clean spam check. An unreadable/unknown check does not release the account and still advances the bounded recovery attempt counter.
- Scheduling/de-correlation lives in pacing/fleet modules; cycle modules own one session; runtime modules own task state, sleep, cancellation and recovery. Keep injectable collaborators behind the warming seam.
- Board/read paths stay bulk-loaded; loop failures are logged and persisted rather than silently killing a task.

Exact caps, timings and implementation headroom belong to config/tests/code, not memory.
