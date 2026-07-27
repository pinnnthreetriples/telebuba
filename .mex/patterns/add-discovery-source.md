---
last_updated: 2026-07-27
---

# Add a Channel-Discovery Source
1. Add the transport to `core/`: a typed read action + dispatcher for MTProto (`core/telegram_client/_read_discovery.py`), or an httpx gateway that never raises for HTTP (`core/telemetr.py`).
2. Add one literal to `DiscoverySource` in `schemas/neurocomment_discovery.py` and its i18n label under `neurocomment.modal.discovery.source.*` (ru + en).
3. Add one adapter in `services/neurocomment/_discovery_providers.py` returning `SourceOutcome`; a missing key is an empty outcome, a failure is `error`, never an exception.
4. Call it from `run_search` in `_discovery_search.py` and give it a `_SOURCE_PRIORITY` rank (lower wins cross-source dedup).
5. Reach the transport only through `services/neurocomment/_seams.py`.
6. Tests: gateway (`tests/core/`), fan-out and dedup (`tests/services/neurocomment/test_discovery_search.py`).

Verify: `services/` imports no telethon/httpx; a failing source degrades the run (`last_error`) instead of aborting it; Telegram RPCs stay behind the jittered pacing in `_pace`; the operator key follows the `warming_settings` keep/clear/replace pattern.
