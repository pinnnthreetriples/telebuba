---
last_updated: 2026-08-06
---

# Add a Channel-Discovery Source

1. Add a typed transport in core/: Telegram reads use gateway actions; HTTP catalogues use a provider gateway that converts HTTP failures to typed outcomes.
2. Add the source literal/schema and RU/EN source label.
3. Adapt the source in the discovery service to SourceOutcome; every skipped/failed source carries a reason instead of disappearing.
4. Route the provider through the neurocomment seam.
5. Merge by per-source rank/interleaving before the global cap. Source priority decides dedup spelling only; it must not starve a lower-priority source.
6. State which filters the source actually honors. A requested filter must never be accepted and then silently ignored by every active source.
7. Preserve the existing candidate set when no relevant source answered; an answered empty result may replace it.
8. Test the gateway contract, source outcomes, filter coverage, dedup/interleaving and failure semantics.

Current source/filter policy lives in context/runtime-discovery.md; provider wire details live in gateway tests/code.
