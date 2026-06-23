---
name: neurocomment
description: Neurocomment runtime — an event-driven post listener that auto-comments on channels with on-prompt AI text, per-event asyncio tasks, campaign/readiness/claim data model, and anti-ban knobs. Load when adding, debugging, or tuning this domain.
triggers:
  - "neurocomment"
  - "comment"
  - "campaign"
  - "listener"
  - "post listener"
  - "discussion group"
edges:
  - target: context/warming.md
    condition: when reusing the readiness/trust health gate or the board read-model pattern
  - target: context/telegram.md
    condition: when the listener or a comment performs Telegram I/O
  - target: context/architecture.md
    condition: when deciding where runtime code lives vs UI code
last_updated: 2026-06-23
---

# Neurocomment Runtime

## What this is

Neurocomment automatically posts a short, on-prompt AI comment under fresh posts of a campaign's channels, using a pool of fleet accounts. A campaign carries a product-mention prompt; each new post in a watched channel is mapped to its campaign, claimed by exactly one healthy under-quota account, answered with a Gemini-generated comment, and posted into the channel's linked discussion group.

## Runtime model

Unlike warming (a per-account continuous loop), neurocomment is **event-driven**:

- One dedicated **listener account** runs a standing post listener (`core.telegram_client.subscribe_posts`) over the active watch set.
- Each surfaced post is handled in its own fire-and-forget `asyncio.Task` so the Telethon listener loop is never blocked; tasks are tracked in `_runtime._TASKS` so shutdown can cancel them.
- `handle_new_post(event)` is the testable on-post pipeline (`engine.py`); it never raises (listener-safe).

Lifecycle entrypoints (`services/neurocomment/_runtime.py`, re-exported from the package):

- `start_neurocomment(listener_account_id)` — persist the listener id, then `reconcile_neurocomment_runtime(id)` to point the listener at the active watch set.
- `stop_neurocomment()` — read the persisted id, `shutdown_neurocomment_runtime(id)`, then clear it.
- `reconcile_neurocomment_on_startup()` / `shutdown_neurocomment_on_shutdown()` — no-arg hooks wired into `app.on_startup` / `app.on_shutdown` in `main.py`; they read the persisted listener id and resume/tear-down accordingly.
- `reconcile_neurocomment_runtime(id)` — idempotent: no active channels → stop the listener; otherwise (re)subscribe.

The active listener account id is the **one piece of runtime state persisted** so reconcile survives a restart (the in-flight per-post tasks and cooldowns are in-memory only).

## On-post pipeline (`engine.py`)

`handle_new_post` → `_handle_new_post`:

1. Map the post's channel to its active campaign (`fetch_active_campaign_for_channel`); none → skip.
2. Filter posts we don't comment on (`_filter_reason`): forwards, media-without-caption, empty, link-only/ad.
3. Select one ready, healthy, under-quota, non-cooled account at random (`_select_account`).
4. Win the atomic `claim_comment(channel, post_id, …)` idempotency gate — losers return (no double comments across concurrent events / restarts).
5. Generate a short on-prompt comment (Gemini, capped at `comment_max_words`, run through the light content checks + dedup), pause a human beat, post it, classify the outcome.

Load-bearing (not optional, even under ponytail): the atomic claim, the account health/quota/cooldown selection gates, and the outer try/except isolating any fault from the listener task.

## Captcha — detect-and-skip MVP

There is no captcha solver. Onboarding (`onboarding.py`) joins each channel's linked discussion group and assumes an OK join is comment-able; the engine **lazily** flips readiness (`joined=True, captcha_passed=False, ready=False`) the first time a comment is actually forbidden (`ChatWriteForbiddenError` / `ChatGuestSendForbiddenError` / `UserBannedInChannelError`). Such a `(account, channel)` is then no longer selected until re-onboarded. Solving entry captchas is deferred (spike #120).

## Data model

DB tables (created in migration #11; the runtime scalar in #12), queried via `core/repositories/neurocomment/`, re-exported through `core/db.py`:

- `neurocomment_campaigns` — campaign + product-mention prompt + status (`active`/`paused`/`archived`).
- `neurocomment_campaign_channels` — channel↔campaign links; a partial unique index enforces **one active campaign per channel**.
- `neurocomment_campaign_accounts` — account↔campaign links (an account may serve many campaigns).
- `neurocomment_linked_groups` — cached resolution of a channel's linked discussion group + comments-enabled flag.
- `neurocomment_readiness` — per-`(account, channel)` `joined` / `captcha_passed` / `ready`.
- `neurocomment_comments` — the per-`(channel, post_id)` claim + outcome (`claimed`/`posted`/`failed`); the claim PK is the idempotency gate.
- `neurocomment_runtime` — single-row (`id=1`) table holding the active `listener_account_id` (NULL = stopped) for restart-reconcile.

## Gateway

- Posts in: `core.telegram_client.subscribe_posts(account_id, channels, on_post)` / `stop_post_listener` (`_listener.py`).
- Reads/actions: `core.telegram_client.execute(...)` / `execute_read(...)` with typed actions (`GetLinkedDiscussionGroup`, `JoinDiscussionGroup`, `CommentOnPost`).
- All Telegram / Gemini / spam-probe / randomness access in the service goes through `services/neurocomment/_seams.py` so a test patches one place.

## Board / read model

`services/neurocomment/board.py` → `load_neurocomment_board(campaign_id)` builds the work-view read model for the UI. Same invariant as warming: it **bulk-loads** accounts, campaign readiness, linked groups, today's posted comments, warming states, spam statuses, and fingerprints once — no per-card DB queries. Account health reuses the warming `evaluate_readiness` gate + `account_trust_score_from`; the only neurocomment-specific logic is the per-channel status derivation (`ready` / `comments_off` / `join_by_request` / `captcha_gated` / `throttled`).

`features/neurocomment/` is UI-only (campaign create, channel pool, account picker, listener selection, Onboard/Start/Stop, board rendering). It delegates all domain logic to `services/neurocomment/` and must not import from `features/warming/`.

## Anti-ban knobs

All tunables live in `core/config.py` under `settings.neurocomment` (no magic numbers): `reply_delay_min/max_seconds`, `join_delay_min/max_seconds`, `max_comments_per_hour`, `max_comments_per_channel_per_day`, `comment_max_words`, `max_retries`, `peer_flood_cooldown_seconds`, `link_only_max_word_chars`, `stop_cancel_timeout_seconds`. `.env.example` must mirror these fields (`tests/test_architecture.py` enforces it).

## Operator-run (out of scope for code)

The live canary on real accounts and the ban-observation calibration are **operator-run** (human-in-the-loop): start with conservative `settings.neurocomment` defaults, run a small canary against real Telegram, watch for spam/ban signals, and tune the knobs. The code never auto-runs a real-account canary.

## Planned — Ф2 anti-detect (specced, not built)

Two deferred guards, resolved via grill (see `context/decisions.md` → Ф2 update). Not yet in code — kept here as the design anchor.

- **Comment-deletion → channel back-off.** A periodic asyncio **sweep** (the lone non-event loop) re-reads recently-posted comments via a new gateway action `CheckMessagesAlive` (`get_messages(ids=[…])`→`None`, NOT the unreliable `MessageDeleted` event). Too many of a channel's comments gone within the window → trip an **escalating in-memory channel cooldown** (mirrors the per-account `_state` cooldown, recomputed each sweep, self-healing), and the engine stops selecting accounts for that channel until it expires.
- **Semantic dedup across accounts.** Local **token-set Jaccard** over normalized text (no Gemini embeddings — hot-path latency), group+window scoped, plugged into the `_generate_acceptable` retry loop after exact-hash. Threshold `0` disables it.
- New knobs (to land with the code, mirrored in `.env.example`): `deletion_sweep_interval_seconds`, `deletion_sweep_lookback_hours`, `channel_backoff_min_deletions`, `channel_backoff_base_seconds`, `channel_backoff_max_seconds`, `semantic_dedup_threshold`, `semantic_dedup_window_hours`.

## What does NOT belong here

- No direct Telethon/SQLAlchemy in `services/` or `features/` — go through `core.telegram_client` / `core/repositories`.
- No business logic in `features/neurocomment/` — it is UI-thin.
- No cross-feature imports (`features/neurocomment/` must not import `features/warming/`).
- No captcha solving (detect-and-skip only at MVP).
