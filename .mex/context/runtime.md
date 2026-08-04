---
last_updated: 2026-08-04
---

# Telegram Runtimes

## Telegram and proxy
- Only `core/telegram_client/` imports Telethon, owns clients/listeners, and returns typed Pydantic results.
- Services choose policy and persist outcomes; never expose Telethon objects or session/tdata contents.
- Device fingerprints are immutable. Proxy credentials resolve inside `core/` from the shared `proxies` pool; one account uses at most one proxy and capacity is config-driven. Proxy checks discover the public exit IP over a TLS tunnel, then persist IPinfo/MaxMind country consensus without exposing credentials.
- Rate limits return classified outcomes; persist cooldowns and never retry immediately.
- `_pool` is the ONLY owner of an account's connection: never build a second client for an account that may already be held. Telethon keeps its `.session` SQLite file in an uncommitted write transaction while connected, so a rival client dies on `sqlite3.OperationalError: database is locked` after pysqlite's 5s timeout — a raw error no route maps, i.e. a bare 500. That is exactly how `/accounts/check` and `/spam-check` broke for whichever account held the neurocomment listener (permanently, since that listener never disconnects). Both probes now borrow via `get_client`; `_session._probe_client` re-raises `TelegramClientPoolError.cause` so the classification ladder still sees the real Telethon/proxy/network error. Only `_auth` login/logout still builds its own client, on an account by definition out of service.
- `_client._session_path` resolves the session file as explicit argument → the account row's stored `session_name` → `account_id`. `session_name` is a genuine path override (migration F5 gives it a UNIQUE index as the anti-clobber guard), and the pool passes no name, so the lookup is what makes the override real. All creation paths currently set it equal to `account_id`, so it changes nothing today; the point is that a divergence would otherwise make every pooled action mint an empty unauthorized session beside a good credential. A NULL column still falls back to `account_id` (F5's remediation depends on that).
- A frozen account stays authorized and `get_me()` succeeds; classify it via `help.getAppConfig` `freeze_since_date` (plus `FrozenMethodInvalidError`, matched by class and ordered above `FloodWaitError`) into the permanent `frozen` status.
- Profile-edit failures surface stable snake_case codes in the error envelope (`username_occupied`, `username_invalid`, `about_too_long`, `account_frozen`, `flood_wait`; media via `ProfileGatewayError`), translated in the SPA under `accounts.profile.code.*`. The typed-action executor persists `frozen` on any action but `flood_wait` only for profile/media edit actions (`_PROFILE_EDIT_ACTION_TYPES` in `_profile.py`) — warming/neurocomment floods are transient pacing events and must not stick to account status; both writes are best-effort (`_mark_account_status`, execute never raises). Photo set/remove/set-main refresh `avatar_thumb`/`avatar_etag` immediately (`refresh_account_avatar`); a partial update re-reads and stores confirmed values, best-effort — live values (e.g. 4-char NFT usernames) may not pass our schemas (#272, follow-up PR).

- **An `ok` `update_profile` does not prove the bio was stored.** `account.updateProfile` answers with a `User`, which carries no `about`, so the ActionResult only witnesses that the RPC did not raise. Telegram silently drops the field on young accounts — measured 2026-07-26 on two accounts whose bio advertised a channel created 12–19 s earlier, on top of a burst of avatar/story/channel writes: five saves, five `telegram_update_profile → success`, nothing stored. The only witness is a live `UserFull.about` read, and the edit modal already forces one after every save; `ProfileModal` compares it against what the save sent and explains the revert instead of letting it read as "the dashboard failed to save". Keep that verdict derived from the pull, never stored: an immediate read can answer with the pre-write value (replication lag), and a stored verdict would be a permanent false alarm. Do NOT persist a "confirmed" bio into `accounts.bio` — it is read nowhere but the modal's initial form default, which the live re-seed overwrites on open, and a lagged read would leave the row wrong with no other writer to heal it.
- `forcePull` and the modal's mount-time snapshot query share ONE query key. The forced pull must `cancelQueries` that key before `setQueryData`, or the slower plain read resolves last and puts pre-mutation fields back — with no automatic re-pull afterwards, that stale state is permanent. Re-check the `pullGen` generation AFTER the cancel await, not only before it.
- An avatar or bio that reached Telegram is NOT the same as one strangers can see: `account.getPrivacy`/`setPrivacy` on `ProfilePhoto` / `About` / `StatusTimestamp` decide that, and a contacts-only photo key is why a correctly uploaded avatar still renders as a letter placeholder to a non-contact while our own live read (`UserFull.profile_photo.id` → `is_main`) reports it present. `core/telegram_client/_privacy.py` owns both directions. The read collapses Telegram's rule vector onto ONE level by returning on the first BASE rule it matches, and the loss is one-directional in the direction that misleads: `[DisallowUsers([...]), AllowAll()]` — "everybody except these accounts" — reports `everybody`, so the tab can CONFIRM a privacy diagnosis but never refute one. `unknown` is reached only by an empty or base-rule-less vector; `[AllowCloseFriends(), DisallowAll()]` reports `nobody`, NOT `unknown` (an earlier note here claimed otherwise). `unknown` can never be sent back. `setPrivacy` REPLACES a key's whole vector, so applying a level deletes that key's exceptions — the confirm dialog says so. `set_privacy_settings` is deliberately NOT in `_PROFILE_EDIT_ACTION_TYPES`: it has a fleet-wide apply, and one press could park a large slice of the fleet in `flood_wait`, which blocks `start_warming` when `enforce_readiness` is on — refusals are reported per account instead. The fleet sweep is a synchronous route behind a MODULE-level semaphore of 4 (per-request would let two overlapping sweeps reach 8) and skips only `health_for_status == "fail"` accounts, naming the status as the reason: the obvious `== "ok"` filter reported `ok: 0` on a farm imported minutes ago, when every row is still `new` — exactly the fleet the action exists for. `execute_read`'s pool/socket arm reports `unavailable: {class}` and nothing more, because `TelegramClientPoolError` stringifies with its cause and a proxy failure's cause names host:port, while that reason is rendered verbatim in the browser by both the profile and privacy error envelopes.

## Warming
- One persisted `asyncio.Task` per active account; FastAPI lifespan starts reconciliation and shutdown.
- Persona sets target cadence; phase/trust remains the safety ceiling.
- `cold_start_spread_hours` (5) is a HARD ceiling on the pre-first-cycle wait and wins over the active-hours window. The snap only ever moves a candidate FORWARD to the account's next local morning, so it fits under the ceiling only when the spread exceeds the quiet window (24 − active window) plus the morning spread; an overshoot falls back to the original random point (not the ceiling — that would fire every such account in the same second). Active hours use the account's *phone* timezone, so for a fleet several zones behind the operator the snap meant a first cycle ~14h out that read as stuck. Measured trade-off at 5h vs a 9h quiet window: the snap survives ~4% of cold starts and ~37% begin in the account's local night. ~13h would restore daytime-only starts; the two cannot both hold. The ceiling covers the first cycle only — a persisted `next_run_at` is honoured verbatim.
- A future persisted `next_run_at` always wins over the cold-start roll, so a config change does not re-time accounts already waiting; `start_warming` writes `next_run_at=None`, so a stop+start is what re-rolls them.
- `phase_daily_cap` does double duty and `intro` at 3 broke both halves. (a) The cycle spends its budget in a fixed order — `set_online` (itself an action) → joins/reads → story glance → inter-account DM — so a cap at or below `expected_actions_per_session` starves the last steps: no first DM regardless of `dm_min_age_hours`, and no story glance either. (b) `persona_next_run_seconds` affords `cap // expected_actions_per_session` sessions and splits the active window between them, so a cap under 2× the divisor collapses to ONE session and a ~15h inter-cycle pause. `intro` is 15 = 3 sessions ≈ a 5h gap, matching `cold_start_spread_hours`, and `settling` follows it to 15 because the cap must never drop as an account ages (`test_compute_intensity_monotonic_in_age`).
- `dm_min_age_hours` (5) counts from OUR row's `created_at`, i.e. hours since import — not the account's real Telegram age. Raise it if the fleet starts collecting DM restrictions. `_DM_ALLOWED_BANDS` (excellent/good/watch) gates on top, and it does NOT block a fresh import: at import the only penalties are new-account (10, ramped) and geo (≤10), so the worst score is 80 = `good`; only `status != 'alive'` (−40) reaches `at_risk`, and readiness already parks those.
- Raising `intro` to `settling`'s cap has two consequences worth knowing before tuning either: `phase_hard_floor_age_hours` (24) becomes a no-op, because the phase drives *only* the daily cap (channel counts, reaction probability and the DM gate are all phase-independent), and `_TRUST_PHASE_CEILING["critical"] = "settling"` means a degraded account of any age inherits the same raised cap.
- `pacing.py`/`_fleet.py` own scheduling and de-correlation; cycle modules own one testable session; runtime modules own state, sleep, cancellation, and recovery. The cycle is `_cycle` (session orchestration) + `_steps` (per-channel reads/reactions/joins and the shared step helpers), split by a plain one-way import — no test patches a `_cycle.<name>` attribute, so no re-export shim is needed; `services.warming._human_delay` now points at `_steps`. Inter-account chat splits the same way: `_chat` owns the dialogue turn (partner choice, send, inbox/tally bookkeeping) and `_chat_text` owns what to say (prompts, transcript context, sanitisation, acceptability/near-duplicate gate, Gemini retry) — patch `recent_pair_messages` and `generate_text` on `_chat_text`.
- Inter-account DMs need the partner's PHONE, not just their `user_id` (#289). A session that never interacted with the peer has no cached `access_hash`, so a raw int raises `ValueError` and every warming DM silently failed. `core/telegram_client/_dm.py` resolves via `contacts.resolvePhone` — deliberately NOT `contacts.importContacts`, which saves a permanent contact nobody deletes and would build each account a saved list of nothing but fleet accounts (the correlated-graph tell `_fleet.py` channel slicing exists to avoid). Both sit behind the same `inputPrivacyKeyAddedByPhone` gate, so reach is identical. Telethon files the `access_hash` from the response, so a cold pair pays `users.GetUsers` + `contacts.resolvePhone` once.
- A peer we can never address (no usable phone, or phone-lookup privacy) raises `DmPeerUnresolvedError`; `_chat.py` matches it BY CLASS NAME (`_PEER_UNRESOLVED`, pinned by a test — `services/` must not import a `core/` private) and skips the turn with `failures=0, attempted_actions=1`: no cycle failure (which would park a healthy sender in terminal `error` over someone else's privacy setting), but the RPCs still bill against the daily cap. Only PERMANENT conditions may map to it — Telethon turns exhausted retries into a bare `ValueError`, and swallowing that consumed live partner messages via `mark_message_replied`.
- Quarantine recovery releases on `clean` only, and EVERY window spends one `quarantine_max_repeats` repeat. `unknown` (a probe that never reached @SpamBot — dead proxy/session — returned uncached by `refresh_spam_status`) must not release the hold, but must not hold it for free either: with the counter frozen an account sat in `quarantine` forever at one WARNING per window and `quarantine_exhausted`, the only route to `error` and the only ERROR alert, was unreachable. The exhausting window picks the reason: `quarantine_exhausted` still asserts a re-checked, still-standing flood; `quarantine_unreadable` says the standing could not be read.
- The pre-cycle reservation (#208) books the whole remaining daily budget and the hand-back reconciles it down. That hand-back is guarded by the BOOKED VALUE (`hand_back_warming_reservation`: `daily_actions = booked AND daily_count_date = date`), never by `run_id`: the stop path clears the generation and writes `idle` the moment its ~5s cancel wait is up, and Start always mints a new one, so any generation-based CAS refuses exactly the hand-back a slow-unwinding cycle owes — which cost the account the rest of the day on a routine Stop→Start. The value guard is also what keeps a dead cycle off a live generation's own booking, and it makes the write idempotent (the first apply invalidates the predicate), which matters because `asyncio.shield` leaves a cancelled hand-back landing as a detached task the loop's exit handler then retries. Keep it an ABSOLUTE write: a relative give-back would double-subtract there and overspend the cap. A hard kill still forfeits the day by design (no handler ran, so the real spend is unknown) — that is the fail-closed direction, do not "recover" it by releasing a booking whose spend nobody witnessed.
- Board reads stay bulk-loaded. Loop failures must be logged and persisted.

## Neurocomment
- A persisted listener watches active campaign channels; each post runs in a tracked task.
- Pipeline: map campaign → filter → choose healthy under-quota account → atomic post claim → generate/deduplicate → delay → comment → persist.
- Challenge handling distinguishes Telegram restrictions from bot challenges and supports configured OpenAI/Gemini text/vision solving, retries, caching, operator actions, and channel backoff.
- Atomic claims prevent duplicate comments; warming and listener roles are mutually exclusive; listener-safe handlers do not leak exceptions.
- Post-outcome families each own a state write: cooldown statuses park the account (channel-scoped for slow mode), `UserBannedInChannelError` sticks a pair ban, `ChannelPrivateError` (`_LOST_ACCESS_ERRORS`) parks the pair with onboarding's `join_failed` sentinel so a later pass re-joins, gate errors flip readiness off and count a solver failure. The named families can never be a complete enumeration — `core/telegram_client/_actions.py` collapses every unmapped Telethon exception into one generic `status="failed"` — so the *default* branch also parks (channel-scoped, duration-less cooldown fallback). Without that a terminal error outside the families re-picked the pair on the channel's next post forever; naming more errors only moves the hole.
- `ChannelPrivateError` recovery is NOT automatic: `_ensure_onboarding_running` has no timer (only operator Start, boot with `listener_running=1`, and the campaign link/deactivate/assign/set-status reconciles). Telethon also raises it on a stale cached entity, so a *transient* access loss parks the pair until an operator action or restart — a deliberate trade against the pre-#279 behaviour, which recovered on its own but retried on every post.
- Cooldowns are removed only by expiry (`in_cooldown` evicts lazily). There is deliberately no early clear: a task past the selection gate would erase a cooldown a rival task had just parked the account with, and the persisted row would outlive the in-memory clear.
- Selection picks the least-busy eligible account (fewest comments this hour) and breaks ties through the rng seam, so load spreads instead of concentrating on one account.
- Semantic dedup is ON by default (`semantic_dedup_threshold` 0.8, token-set Jaccard) on top of the exact-hash reservation; the vision solver has its own longer budget (`challenge_vision_timeout_seconds` 45s) because the text 10s cutoff truncated image captchas the provider was still answering.
- The append-only tables (comments/challenges/join log) are pruned by `retention_days` (90, 0 = keep forever) on the deletion-sweep tick, at most once per `retention_prune_interval_hours`. In-flight `claimed` rows and `solved` challenge rows are never purged — the latter ARE the decision cache. The prune rides the sweep, so disabling the sweep disables retention too. The cutoff is floored at one day: `retention_days` is a float, and a sub-day cutoff deletes join-log rows the rolling-24h cap still counts, which makes the cap under-report and lets an account exceed it.
- Anti-freeze joins (#270): every channel-join site (campaign onboarding, operator/retry, listener) paces with a jittered delay, breaks the burst on FloodWait/cooldown, and is gated by a persisted rolling-24h per-account cap (`neurocomment_join_log`, `max_joins_per_account_per_day`, default 20). `already_participant` no-op re-joins are a success but not counted. The cap counts NC joins only (warming joins uncounted → same-day carryover uncounted).
- The listener join pass runs as a single-flighted background task (coalescing rerun, mirrors onboarding), so reconcile returns off the request/lock path; peer-id resolution is cached across reconciles.
- `subscribe_posts` returns the channels it actually registered; a channel it cannot resolve to a peer id is silently absent from the filter, so reconcile diffs requested vs subscribed into `_UNWATCHED_CHANNELS`, logs it, and the runtime status exposes it (`active_channels` counts only watched ones). Without that the board painted such a channel `ready` while no post could ever arrive.
- That report is only true if published atomically. `_publish_unwatched` (`_watch.py`) does `clear()` + `update()` with no await between them, at every mutation site. Clearing on entry and refilling after `await subscribe_posts` let a status poll land mid-reconcile and see an empty set (every channel reported watched), and made the set the UNION of overlapping unlocked reconciles rather than the truth of the pass whose handler survived. Reconcile lives in `_watch.py` precisely because publish, subscribe and re-subscribe must stay in one await-free step.
- Two paths the report used to lie on: a listener account that is also warming is unsubscribed but keeps `listener_running` set (the operator paused nothing), so that branch publishes the WHOLE watch set as unwatched — `running=True` with `active_channels=0` is a legitimate "up but deaf" report. And `subscribe_posts` necessarily runs BEFORE the paced joins, so a not-yet-joined channel cannot resolve; the join task's tail re-subscribes once the joins drain (re-subscribe only — a reconcile from there would make `_ensure_join_running` see its own task alive and leak the rerun flag). `_PEER_IDS` never caches failures, so an unresolved channel is always re-attempted; the inverse (resolved once, access lost later) is still unreported.
- Per-post hot path is O(campaign candidates), not O(fleet): candidate-scoped signal reads (both bulk quota readers take `account_ids` — without it the hourly count SCANs every comment ever written; measured 29.3ms → 0.13ms on 200k rows, and the win exists because production has no `sqlite_stat1`: adding `ANALYZE` would let SQLite skip-scan the old form and make the perf rationale stale, though the scoping rationale holds), channel-scoped readiness (`list_channel_readiness`), narrow single-account quota re-check, settings loaded once per post. The deletion sweep buckets channels by campaign in one query.
- The board is a read model only: no trust/health/spam derivation (the SPA reads those from `AccountRead`), and the card's quota denominator comes from the saved settings row, not the config default.
- File-size gate (aislop max 400) drives the `_runtime`/`onboarding` splits into `_join`/`_lifecycle`/`_classify`/`_sweep`/`_watch` via E402 re-export-after-body; task-handle globals stay in `_runtime` (tests rebind them, and rebinding a re-exported name does not reach the defining module), so a peer module reaches back through the `_runtime` module object. `_generate.py` now sits ~5 lines under the cap. Repository reads split the same way (`_readiness`, `_retention` beside `_comments`/`_quota`), and request models live in `schemas/_neurocomment_requests.py` re-exported from `schemas/neurocomment.py` — a pure module move, so the generated OpenAPI client is unaffected (component names are class names). `schemas/neurocomment.py` sits ~8 lines under the cap: the settings pair is the next extraction.

Channel discovery (`services/neurocomment/discovery.py`) is a background run per campaign,
single-flighted in memory (`_discovery_state`), cancelled on shutdown AND when its campaign is
deleted. The account is resolved FIRST, then `try_reserve` claims the campaign slot, the
account, and one unit of the rolling-24h allowance in a single await-free step — the same
check→spawn rule `_ensure_onboarding_running`/`_ensure_join_running` follow. Claiming the
account matters as much as the campaign: every campaign resolves to the same fleet listener, so
per-campaign alone would allow N parallel streams on one account. Resolving before claiming is
also what removes any window where a failure could strand a claim.
Discovery refuses an account that is warming (`list_warming_account_ids`) — warming's freeze
avoidance assumes it owns its accounts' traffic — and it now RECORDS its own FloodWait as a
cooldown, so the retry and the post-adopt reconcile stay off that account.
Both stages are paced, but only the Telegram arm: the keyword sweep fans out with the same
jitter as the qualification loop, while the catalogue queries run concurrently alongside it
because HTTP to a third party spends no Telegram flood budget. Awaited serially inside the
paced loop they were the stage's real cost (~7 min worst case against the ~20s the pacing
implies), and a Telegram FloodWait used to cancel them too — it now stops the Telegram arm
alone. The operator's key is read once per run, not once per keyword (each read decrypts a
secret), and each keyword signals progress so the modal does not look frozen. Comments-enabled is `channelFull.linked_chat_id`, resolved through the
existing `GetLinkedDiscussionGroup` action; the shared `neurocomment_linked_groups` cache is
read in ONE bulk query and freshness (`discovery_linked_group_ttl_hours`, default 168h) is
applied in the SERVICE, not the repository — onboarding and the board still want the raw cache.
A FloodWait aborts a qualification pass and leaves the tail `pending` (`qualified_at` makes it
resumable); the run never takes `account_lock`, because holding it for minutes would block
warming/neurocomment start-stop for that account. Filter coverage is the feature's sharpest edge and it is not symmetric: `language`/`country`
reach Telemetr.io ALONE — Telegram's own search and its similar-channels feed have no locale
filters, by API design — while `members_min`/`members_max` are filtered server-side by Telemetr
and re-applied to native hits once a count is known. So the request refuses locale filters
without `use_telemetr` (accepting them answered 202 for a run in which they reached nothing),
the form disables the selects, and `_merge` INTERLEAVES by each source's own rank before
applying `discovery_max_candidates`. `_SOURCE_PRIORITY` governs dedup spelling only: sorting the
whole union by it and then capping put the sole filter-aware source permanently at the tail, so
a native sweep that filled the cap dropped every catalogue row and the operator's country and
language influenced nothing at all.
``catalogue_only`` drops the Telegram arm entirely, leaving the one source that honours the
locale filters (and is refused without ``use_telemetr``, which would leave no source at all).
Measured on the real cap (100, catalogue 30/keyword): at four or more keywords this costs ZERO
rows and takes the locale-verified share from ~50% to 100%; at one keyword, or on a filter the
catalogue barely matches, it is 3 rows against 23. Hence off by default — and both Telegram
arms still report ``skipped``, because a source that vanished without saying so is the original
bug. A terminally failing catalogue (revoked key, lapsed plan, spent quota, unknown filter value)
blocks EVERY run while a locale filter is set, since storing unfiltered rows over a filtered set
is a downgrade; the board names the way out rather than looping the operator through their daily
search slots. Only those reasons — a rate limit or a network blip is also `failed`, but there the
answer is to retry, not to drop the filters. Note a catalogue-only run still spends one of the
twenty daily search slots while making zero Telegram reads.
Telemetr.io is optional: no key means a skipped source — but a REPORTED one, because silence let
the run reach `done` as if the filter had applied. Every source's outcome (`ran`/`failed`/
`skipped`, hits, kept, exclusive, truncated, reason, gateway detail) rides
`DiscoveryProgress.sources` to the board and into the finish event — ALWAYS, including a run
that stored nothing, which is exactly when the operator needs to see which source refused; the per-row catalogue geo rides the board too, but is deliberately NOT
persisted (no column, and a migration against the live database needs operator approval), so a
board read after a restart falls back to the row's single stored source.
Writing candidates is delete-then-insert, so the set is replaced only when at least one source
actually answered (`SourceOutcome.answered`); if none did, the previous set survives and the run
ends `failed`. A source answering with zero hits — or a filter removing every hit — IS an empty
result and does replace it. One exception, and it is the point: when locale filters were asked
for and the catalogue did not answer, the run does NOT replace the stored set, because
overwriting a filtered, already-qualified set with unfiltered native rows is a silent downgrade.
A FloodWait aborts the search sweep as well as qualification, and it now also SKIPS qualification
outright — the search stage has already written the cooldown, nothing between the two re-checks
it, and probing inside a live window is how Telegram turns a soft limit into a hard one.
Telemetr.io's wire contract, verified against its live OpenAPI document, because getting it wrong
cost this feature every catalogue result it ever returned: `peer` is NOT a handle, it is a
`["Group","Channel"]` chat-type enum, and `/catalog/search` items carry no handle at all (no
`peer`, no `link`, no `username` — the API has no `username` property anywhere). A `CatalogItem`
gives `internal_id`, and the handle comes from a second call, `/channels/info-batch` (`ids`
comma-separated, max 100), whose `ChatInfo.link` is a `https://t.me/<handle>` URL — so one
catalogue page costs one extra request against a 1000/month free tier. `country` is NOT an ISO
alpha-2 code but a `/dictionaries/countries` id (slug, e.g. `turkey`), resolved in `core.telemetr`
from the codes the UI offers; an unresolvable value is a reported terminal status, never a silent
empty page. Quota exhaustion is 426 and an inactive subscription is 412; 429 is not documented for
this endpoint at all, and 4xx is never retried because a retry spends another billable unit. A pass
also stops when failures reach HALF of at least 20 probes: the consecutive counter catches a dead
session but not a half-dead one, and the bound has to be a RATE, not a count — a re-search
re-inserts every candidate with `qualified_at = NULL`, so a fixed count aborts at the same handle
on every retry and the tail past it becomes unreachable forever. Deliberately not a knob.

Campaign-channel ownership is folded: `@Name`, `name` and `NAME` are ONE channel, `+HASH` invite
keys stay EXACT (they really are case-sensitive, so a plain `COLLATE NOCASE` would wrongly reject a
second invite link). The fold has exactly two spellings, kept in step by a test:
`core.channel_tokens.dedup_key` and `channel_fold_sql` — and the read path evaluates it **in SQL on
both sides**, never pre-folding the probe in Python, because SQLite's `lower()` is ASCII-only while
Python's is full-Unicode and a mixed comparison leaves a committed row that no lookup can find.
Spelling the query as the index's own expression is also what lets it SEARCH rather than SCAN, so
do not "simplify" it to `lower(col) = ?`. Migration 39 creates the folded index
(`ix_nc_channel_one_active_campaign_fold`) BEFORE dropping #11's — pysqlite gives DDL no
transaction, so drop-first would leave the table unconstrained if the create failed. It deactivates
(never deletes) pre-existing case duplicates so the unique index can be created at all, folding in
SQL so it cannot demote a pair the index would have accepted. `LinkChannelRequest` canonicalises
the handle, which is why `_deactivate_channel` fold-matches too — otherwise removing a legacy
`@news` row would 204 while leaving it active.

Removing an account tombstones it in the client pool (`removing_client`, reference-counted) for the
whole evict → unlink → delete sequence. Pool borrowers do not take `account_lock`, so without the
tombstone one could rebuild a client mid-removal, reopen the session file and make the unlink fail
on Windows — aborting before the DB row was deleted — or re-create the file afterwards.
`get_client` checks the mark in THREE places and all three are load-bearing: before the lock
(cached fast path), inside it (a borrower that queued before the mark, which FIFO puts ahead of the
removal), and after the build (a borrower already inside the lock when the mark went up — that one
otherwise publishes a live client).

Everything is now inside that fence. `log_out_session` tombstones REGARDLESS of `wipe_session`:
Telethon's own `log_out()` ends in `session.delete()`, and the flag only decides whether the
removal is *guaranteed*, not whether the file is touched. Both probes borrow FROM the pool
(`_session._probe_client`, `_spam.check_spam_status`), so they are covered by the mark like any
other borrower. `_auth`'s three flows — request-code, submit-code, logout — still build their own
client, so on top of `removing_client` they run under `_auth_lock` (`_AUTH_LOCKS`, one
lazily-created `asyncio.Lock` per account, taken OUTSIDE `removing_client`, which holds no lock
across its `yield`, so the two cannot deadlock in either order). The tombstone alone refuses POOL
rebuilds and therefore did not fence those three against EACH OTHER: measured, a logout started
while a request-code was in flight put two Telethon clients on one `.session` file. A failed unlink
still skips `delete_account`, keeping the removal retryable.

`_client._session_dir_child` is the single sink every Telethon open and `_auth`'s unlink pass
through, and it refuses a name that is not one plain child of the session dir. The verdict is
reached LEXICALLY — empty, leading `.`, `..` anywhere, or either separator — with the
resolved-parent comparison kept only as the symlink backstop. Deciding on `resolve()` alone was
both platform-dependent (Win32 strips trailing dots and reads `\` as a separator, POSIX does
neither, so `...` and `..\evil` got opposite verdicts by OS) and filesystem-state dependent
(`resolve()` cannot collapse `..` against a directory that is absent, so `...`/`.. `/`. ` were
allowed on first boot — reachable, because `remove_account_session` is the one caller that skips
`_ensure_session_dir()`). `_ACCOUNT_ID_PATTERN` (`^[A-Za-z0-9_-][A-Za-z0-9._-]*$`) is the second
line of defence, not the guard: its first character class excludes `.` on purpose, because
`Path("..session").stem` is `"."` and the session-file import derives the id from that stem, so
`account_id="."` used to name `<parent>/sessions.session` — one level ABOVE the directory, beside
the database.

`create_telegram_client` sets `parse_mode = None` on BOTH build branches. Telethon defaults to
markdown, which silently eats `**bold**`, `__italic__`, `~~strike~~`, `` `code` `` and
`[text](url)` out of every send — and a channel post read back → prefilled → re-saved persists the
degraded text. It is not every Telethon client in the process (`core.tdata_import` gets one from
opentele2's `ToTelethon`), but it is every SENDING one. The consequence lands in
`services.content.strip_markdown_delimiters`, which puts back what the parser used to do — for
GENERATED text only — by removing PAIRED delimiters and the `[text](url)` form, and deliberately
leaving unpaired markers alone (Telethon left `snake_case__name` alone too).

API/frontend contain no runtime policy. Telegram/provider access uses gateway seams; durability comes from persisted domain state and restart reconciliation, not an outbox.
