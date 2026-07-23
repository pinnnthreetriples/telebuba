---
last_updated: 2026-07-16
---

# Change Warming Runtime
1. Update boundary contracts in `schemas/warming.py`.
2. Put logic in the owning board/settings, pacing/fleet, cycle, transition/loop or runtime module.
3. Persist through the warming repository; keep timing injectable.
4. Classify/log failures so tasks cannot die silently.
5. Test normal behavior, persistence/restart, failure and cancellation.
6. Run relevant backend gates.

Verify: no scheduler or API/UI policy; board stays bulk-loaded; Telegram uses typed actions; stop/reconcile is bounded; consider counter defect `#208`.
