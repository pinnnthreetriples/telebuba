---
last_updated: 2026-07-25
---

# Telegram Runtimes

## Telegram and proxy
- Only `core/telegram_client/` imports Telethon, owns clients/listeners, and returns typed Pydantic results.
- Services choose policy and persist outcomes; never expose Telethon objects or session/tdata contents.
- Device fingerprints are immutable. Proxy credentials resolve inside `core/` from the shared `proxies` pool; one account uses at most one proxy and capacity is config-driven. Proxy checks discover the public exit IP over a TLS tunnel, then persist IPinfo/MaxMind country consensus without exposing credentials.
- Rate limits return classified outcomes; persist cooldowns and never retry immediately.
- A frozen account stays authorized and `get_me()` succeeds; classify it via `help.getAppConfig` `freeze_since_date` (plus `FrozenMethodInvalidError`, matched by class and ordered above `FloodWaitError`) into the permanent `frozen` status.
- Profile-edit failures surface stable snake_case codes in the error envelope (`username_occupied`, `username_invalid`, `about_too_long`, `account_frozen`, `flood_wait`; media via `ProfileGatewayError`), translated in the SPA under `accounts.profile.code.*`. The typed-action executor persists `frozen` on any action but `flood_wait` only for profile/media edit actions (`_PROFILE_EDIT_ACTION_TYPES` in `_profile.py`) — warming/neurocomment floods are transient pacing events and must not stick to account status; both writes are best-effort (`_mark_account_status`, execute never raises). Photo set/remove/set-main refresh `avatar_thumb`/`avatar_etag` immediately (`refresh_account_avatar`); a partial update re-reads and stores confirmed values, best-effort — live values (e.g. 4-char NFT usernames) may not pass our schemas (#272, follow-up PR).

## Warming
- One persisted `asyncio.Task` per active account; FastAPI lifespan starts reconciliation and shutdown.
- Persona sets target cadence; phase/trust remains the safety ceiling.
- `pacing.py`/`_fleet.py` own scheduling and de-correlation; cycle modules own one testable session; runtime modules own state, sleep, cancellation, and recovery. The cycle is `_cycle` (session orchestration) + `_steps` (per-channel reads/reactions/joins and the shared step helpers), split by a plain one-way import — no test patches a `_cycle.<name>` attribute, so no re-export shim is needed; `services.warming._human_delay` now points at `_steps`.
- Board reads stay bulk-loaded. Loop failures must be logged and persisted. Known counter defect: `#208`.

## Neurocomment
- A persisted listener watches active campaign channels; each post runs in a tracked task.
- Pipeline: map campaign → filter → choose healthy under-quota account → atomic post claim → generate/deduplicate → delay → comment → persist.
- Challenge handling distinguishes Telegram restrictions from bot challenges and supports configured OpenAI/Gemini text/vision solving, retries, caching, operator actions, and channel backoff.
- Atomic claims prevent duplicate comments; warming and listener roles are mutually exclusive; listener-safe handlers do not leak exceptions.
- Post-outcome families each own a state write: cooldown statuses park the account (channel-scoped for slow mode), `UserBannedInChannelError` sticks a pair ban, `ChannelPrivateError` (`_LOST_ACCESS_ERRORS`) parks the pair with onboarding's `join_failed` sentinel so the next pass re-joins, gate errors flip readiness off and count a solver failure. An unclassified error must never leave state untouched — that re-picks the pair on the channel's next post forever.
- Cooldowns are removed only by expiry (`in_cooldown` evicts lazily). There is deliberately no early clear: a task past the selection gate would erase a cooldown a rival task had just parked the account with, and the persisted row would outlive the in-memory clear.
- Selection picks the least-busy eligible account (fewest comments this hour) and breaks ties through the rng seam, so load spreads instead of concentrating on one account.
- Semantic dedup is ON by default (`semantic_dedup_threshold` 0.8, token-set Jaccard) on top of the exact-hash reservation; the vision solver has its own longer budget (`challenge_vision_timeout_seconds` 45s) because the text 10s cutoff truncated image captchas the provider was still answering.
- The append-only tables (comments/challenges/join log) are pruned by `retention_days` (90, 0 = keep forever) on the deletion-sweep tick, at most once per `retention_prune_interval_hours`. In-flight `claimed` rows and `solved` challenge rows are never purged — the latter ARE the decision cache. The prune rides the sweep, so disabling the sweep disables retention too.
- Anti-freeze joins (#270): every channel-join site (campaign onboarding, operator/retry, listener) paces with a jittered delay, breaks the burst on FloodWait/cooldown, and is gated by a persisted rolling-24h per-account cap (`neurocomment_join_log`, `max_joins_per_account_per_day`, default 20). `already_participant` no-op re-joins are a success but not counted. The cap counts NC joins only (warming joins uncounted → same-day carryover uncounted).
- The listener join pass runs as a single-flighted background task (coalescing rerun, mirrors onboarding), so reconcile returns off the request/lock path; peer-id resolution is cached across reconciles.
- `subscribe_posts` returns the channels it actually registered; a channel it cannot resolve to a peer id is silently absent from the filter, so reconcile diffs requested vs subscribed into `_UNWATCHED_CHANNELS`, logs it, and the runtime status exposes it (`active_channels` counts only watched ones). Without that the board painted such a channel `ready` while no post could ever arrive.
- Per-post hot path is O(campaign candidates), not O(fleet): candidate-scoped signal reads (both bulk quota readers take `account_ids` — without it the hourly count SCANs every comment ever written), channel-scoped readiness (`list_channel_readiness`), narrow single-account quota re-check, settings loaded once per post. The deletion sweep buckets channels by campaign in one query.
- The board is a read model only: no trust/health/spam derivation (the SPA reads those from `AccountRead`), and the card's quota denominator comes from the saved settings row, not the config default.
- File-size gate (aislop max 400) drives the `_runtime`/`onboarding` splits into `_join`/`_lifecycle`/`_classify`/`_sweep` via E402 re-export-after-body; task-handle globals stay in `_runtime` (tests rebind them). Repository reads split the same way (`_readiness`, `_retention` beside `_comments`/`_quota`), and request models live in `schemas/_neurocomment_requests.py` re-exported from `schemas/neurocomment.py` — a pure module move, so the generated OpenAPI client is unaffected (component names are class names). `schemas/neurocomment.py` sits ~8 lines under the cap: the settings pair is the next extraction.

API/frontend contain no runtime policy. Telegram/provider access uses gateway seams; durability comes from persisted domain state and restart reconciliation, not an outbox.
