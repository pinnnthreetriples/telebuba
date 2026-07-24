---
last_updated: 2026-07-24
---

# Telegram Runtimes

## Telegram and proxy
- Only `core/telegram_client/` imports Telethon, owns clients/listeners, and returns typed Pydantic results.
- Services choose policy and persist outcomes; never expose Telethon objects or session/tdata contents.
- Device fingerprints are immutable. Proxy credentials resolve inside `core/` from the shared `proxies` pool; one account uses at most one proxy and capacity is config-driven. Proxy checks discover the public exit IP over a TLS tunnel, then persist IPinfo/MaxMind country consensus without exposing credentials.
- Rate limits return classified outcomes; persist cooldowns and never retry immediately.
- A frozen account stays authorized and `get_me()` succeeds; classify it via `help.getAppConfig` `freeze_since_date` (plus `FrozenMethodInvalidError`, matched by class and ordered above `FloodWaitError`) into the permanent `frozen` status.

## Warming
- One persisted `asyncio.Task` per active account; FastAPI lifespan starts reconciliation and shutdown.
- Persona sets target cadence; phase/trust remains the safety ceiling.
- `pacing.py`/`_fleet.py` own scheduling and de-correlation; cycle modules own one testable session; runtime modules own state, sleep, cancellation, and recovery.
- Board reads stay bulk-loaded. Loop failures must be logged and persisted. Known counter defect: `#208`.

## Neurocomment
- A persisted listener watches active campaign channels; each post runs in a tracked task.
- Pipeline: map campaign → filter → choose healthy under-quota account → atomic post claim → generate/deduplicate → delay → comment → persist.
- Challenge handling distinguishes Telegram restrictions from bot challenges and supports configured OpenAI/Gemini text/vision solving, retries, caching, operator actions, and channel backoff.
- Atomic claims prevent duplicate comments; warming and listener roles are mutually exclusive; listener-safe handlers do not leak exceptions.
- Anti-freeze joins (#270): every channel-join site (campaign onboarding, operator/retry, listener) paces with a jittered delay, breaks the burst on FloodWait/cooldown, and is gated by a persisted rolling-24h per-account cap (`neurocomment_join_log`, `max_joins_per_account_per_day`, default 20). `already_participant` no-op re-joins are a success but not counted. The cap counts NC joins only (warming joins uncounted → same-day carryover uncounted).
- The listener join pass runs as a single-flighted background task (coalescing rerun, mirrors onboarding), so reconcile returns off the request/lock path; peer-id resolution is cached across reconciles.
- Per-post hot path is O(campaign candidates), not O(fleet): candidate-scoped signal reads, narrow single-account quota queries, settings loaded once per post. The deletion sweep buckets channels by campaign in one query.
- File-size gate (aislop max 400) drives the `_runtime`/`onboarding` splits into `_join`/`_lifecycle`/`_classify`/`_sweep` via E402 re-export-after-body; task-handle globals stay in `_runtime` (tests rebind them).

API/frontend contain no runtime policy. Telegram/provider access uses gateway seams; durability comes from persisted domain state and restart reconciliation, not an outbox.
