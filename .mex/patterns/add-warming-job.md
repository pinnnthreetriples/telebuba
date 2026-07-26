---
last_updated: 2026-07-26
---

# Change Warming Runtime
1. Update boundary contracts in `schemas/warming.py` — settings fields live in its `_warming_settings.py` sibling and are re-exported, so import stays `from schemas.warming import …`.
2. Put logic in the owning board/settings, pacing/fleet, cycle, transition/loop or runtime module.
3. Persist through the warming repository; keep timing injectable through `services/warming/_seams.py`.
4. Classify/log failures so tasks cannot die silently.
5. Test normal behavior, persistence/restart, failure and cancellation.
6. Run relevant backend gates.

Verify: no scheduler or API/UI policy — warming is a continuous randomised per-account loop, one `asyncio.Task` per account in `_runtime._RUNTIME`, and `run_one_cycle` is the testable unit; board stays bulk-loaded (all rows fetched once in `board.py`, no per-card N+1); Telegram uses typed actions; stop/reconcile is bounded; consider counter defect `#208`.
