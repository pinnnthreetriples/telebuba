---
last_updated: 2026-08-04
---

# Change Warming Runtime
1. Update boundary contracts in `schemas/warming.py` — settings fields live in its `_warming_settings.py` sibling and are re-exported, so import stays `from schemas.warming import …`.
2. Put logic in the owning board/settings, pacing/fleet, cycle, transition/loop or runtime module.
3. Persist through the warming repository; keep timing injectable through `services/warming/_seams.py`.
4. Classify/log failures so tasks cannot die silently.
5. Test normal behavior, persistence/restart, failure and cancellation.
6. Run relevant backend gates.

Verify: no API/UI policy in warming; Telegram uses typed actions; task stop/reconcile is bounded; board/read paths stay bulk-loaded. Runtime invariants live in `context/runtime-warming.md`.
