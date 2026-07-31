---
last_updated: 2026-07-30
---

# Add a Channel-Discovery Source
1. Add the transport to `core/`: a typed read action + dispatcher for MTProto (`core/telegram_client/_read_discovery.py`), or an httpx gateway that never raises for HTTP (`core/telemetr.py`).
2. Add one literal to `DiscoverySource` in `schemas/neurocomment_discovery.py` and its i18n label under `neurocomment.modal.discovery.source.*` (ru + en).
3. Add one adapter in `services/neurocomment/_discovery_providers.py` returning `SourceOutcome`; a skipped source carries `state="skipped"` *and* a reason, a failure carries `state="failed"`, never an exception. A source that returns nothing while reporting nothing is the failure mode this whole file exists to prevent.
4. Call it from `run_search` in `_discovery_search.py` and give it a `_SOURCE_PRIORITY` rank. Priority governs **cross-source dedup only** — which spelling of a handle wins. It must never govern truncation: `_merge` interleaves by each source's own rank before applying `discovery_max_candidates`, because sorting the whole union by priority and then capping starved the lowest-ranked source to zero rows on any run that filled the cap.
5. Reach the transport only through `services/neurocomment/_seams.py`.
6. Tests: gateway (`tests/core/`), fan-out and dedup (`tests/services/neurocomment/test_discovery_search.py`).

Verify: `services/` imports no telethon/httpx; a failing source degrades the run (`last_error`) instead of aborting it; Telegram RPCs stay behind the jittered pacing in `_pace`; the operator key follows the `warming_settings` keep/clear/replace pattern.

Filter coverage — check before shipping, this is where the feature has drawn blood:
- Every request filter must state which sources honour it. `language`/`country` reach Telemetr.io alone (Telegram's own search has no locale filters, by API design), so `DiscoverySearchRequest` refuses them without `use_telemetr` and the form disables the selects. `members_min`/`members_max` are filtered server-side by Telemetr and re-applied to native hits once a count is known — so they are fleet-wide. Do not describe the two groups in one sentence.
- A new source that cannot honour an existing filter must not be able to crowd out one that can. Assert it: with the cap filled by the new source, the filter-aware source still keeps rows.
- Whatever the source did lands in `DiscoveryProgress.sources` (`ran`/`failed`/`skipped`, hits, kept, reason). A run that reaches `done` while a filter reached nothing has to be visible on the board, not only in the log.
- The reason code needs copy in `neurocomment.modal.discovery.results.reason.*` (ru + en). Unmapped codes fall back to the raw string, which is what the operator used to read.
