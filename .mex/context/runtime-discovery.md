---
last_updated: 2026-08-06
---

# Campaign Channel Discovery

- Discovery is operator-triggered background work, single-flighted per campaign and account; shutdown/campaign deletion cancel it. Reserve the chosen account before spawning competing Telegram work.
- A discovery account that is warming or in cooldown is not eligible. Telegram pacing/flood budget applies to Telegram work; third-party catalogue HTTP does not consume it.
- Native Telegram search/similar-channel sources do not provide country/language filtering. Locale filters therefore require the Telemetr source; subscriber bounds can be applied to all sources once counts are known.
- Merge sources by per-source rank/interleaving before the global candidate cap. Source priority decides dedup spelling, not which source is allowed to fill the entire cap.
- Every source reports an explicit `ran`/`failed`/`skipped` outcome. Replace stored candidates only when at least one relevant source actually answered. If locale filtering was requested and the locale-aware catalogue did not answer, preserve the existing set rather than silently replacing it with unfiltered native rows.
- Qualification is resumable and stops Telegram work on FloodWait/cooldown. Do not hold the account lifecycle lock across a multi-minute discovery run.
- Provider wire details, endpoint quotas and plan limits are intentionally absent here; `core/telemetr.py`, schemas and gateway tests are the source of truth and can change without turning project memory stale.
- Per-run source progress and catalogue-only metadata that are not persisted are ephemeral across process restart; adding durability requires an explicit schema/migration decision.
