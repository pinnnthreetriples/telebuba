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
last_updated: 2026-06-28
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

## Captcha — proactive challenge solver (Ф2, specced via grill 2026-06-24, #120)

The Ф1 detect-and-skip MVP is replaced by a proactive solver that runs **inside `_onboard_pair` immediately after `JoinDiscussionGroup`**. The lazy-on-comment-failure model from Ф1 is dropped: guardian bots auto-delete their prompt after the 60–120 s answer window, so a post-mortem look-up at comment time would find nothing in 90%+ of cases (the click would also return `BUTTON_DATA_INVALID` on a stale callback).

### State-model split (was: single `captcha_gated`)

The Ф1 single `captcha_gated` state conflated two unrelated failure modes; Ф2 separates them so the solver runs only where it can help:

- **`chat_restricted`** — Telegram-level write block surfaced as `ChatWriteForbiddenError` / `ChatGuestSendForbiddenError` / `UserBannedInChannelError`. Set by chat config (new-member mute, restrict-until-approve) or account status (banned). **Not solvable** — solver is never invoked; retry policy is time-based (24 h cool-down) and may end in permanent skip.
- **`bot_challenge`** — third-party guardian-bot inline-button challenge (Shieldy / MissRose / Combot). Telegram permits writes; the bot kicks if its prompt isn't satisfied. **Solvable** — solver runs.
- **`bot_challenge_backoff`** — derived channel state: when `channel_challenge_backoff_min_failures` accounts in a row failed on the same channel, the channel is cooled down (mirrors #131 deletion back-off; same `_state.py`, separate counter; `base * 2^trips`, capped at `max`).

`_avoid_: "captcha"` as a code identifier — too overloaded; the canonical pair is `bot_challenge` (the thing) + `challenge_solver` (the thing that solves it).

### Solver pipeline (`services/neurocomment/challenge.py`)

`solve_if_present(account_id, group_id) -> ChallengeOutcome ∈ {"no_challenge", "solved", "give_up", "failed"}` is called by `_onboard_pair` right after a successful join:

1. **Wait for the challenge.** New typed read-action `WaitForBotChallenge(chat_id, timeout_s)` (`core/telegram_client/_read.py`) opens a short-lived `events.NewMessage` subscription scoped to the joined discussion group and returns the first matching `BotChallengeMessage` or `None` on timeout (`settings.neurocomment.challenge_wait_timeout_seconds`, default 20). **Predicate (false-negative > false-positive):** sender is a bot ∧ message has `ReplyInlineMarkup` ∧ (`@my_username` in text ∨ `tg://user?id={my_id}` entity ∨ `reply_to` points at a `MessageActionChatAddUser` for our user_id). No match before timeout → `"no_challenge"`, mark `ready`.
2. **Image early-exit.** Photo attached → write the audit row with `outcome='give_up'` and return `"give_up"` (vision in Phase 2; image-challenge counter is the data signal for "build vision or blacklist").
3. **Cache lookup.** `SELECT decision_json FROM neurocomment_challenges WHERE challenge_hash=? AND outcome='solved' ORDER BY decided_at DESC LIMIT 1`, where `challenge_hash = sha256(normalize(text) | sorted(button_labels))`. Hit → skip Gemini, reuse decision (global by hash, not by `(account, channel)`).
4. **Gemini.** Call `_seams.generate_text` with `response_schema_json` set to the `ChallengeDecision` JSON-Schema (`schemas/challenge.py`): `action ∈ {click_button, send_text, give_up}` + `button_index` + `text` + `confidence: float` + `reasoning: str ≤ 200`. Hard timeout `challenge_gemini_timeout_seconds` (default 10 s); timeout / parse-fail → `"give_up"`.
5. **Humanize.** `asyncio.sleep(random.lognormvariate(...))` clamped to `[click_delay_min, click_delay_max]` (default 3–6 s).
6. **Act.** Existing typed `ClickButton` (already implemented) or a plain `send_message` to the discussion group.
7. **Persist `outcome='pending'`** in `neurocomment_challenges`. The engine resolves `pending` → `solved` / `failed` on the first comment attempt for `(account, channel)`.

### Failure escalation

- **Per `(account, channel)`: one shot.** Failed solver marks the pair `bot_challenge`; no retry on the same account. The campaign's other accounts onboard independently and may succeed on their own — natural redundancy through the pool (no cascade).
- **Per channel: K-failure back-off** (see `bot_challenge_backoff` above). Engine stops onboarding new accounts on a cooled-down channel until the back-off expires.
- **Verification through the engine, not the solver.** Solver does not wait for a post-click "welcome" message; ground-truth is the next comment attempt (comment succeeds → `outcome='solved'`; gate-error → `outcome='failed'`).

### Data — migration #14

One new audit-and-cache table, one new column, in a single migration:

- `neurocomment_challenges` — `(id, challenge_hash, account_id, channel, raw_text, button_labels_json, decision_json, outcome, decided_at, outcome_at)` + index `(challenge_hash, outcome)` (cache fast-path) + index `(account_id, channel, decided_at DESC)` (engine outcome resolution).
- `neurocomment_campaigns.solver_enabled BOOLEAN DEFAULT NULL` — per-campaign override; `NULL` defers to the global `settings.neurocomment.challenge_solver_enabled`.

### Config knobs (mirrored in `.env.example`)

`settings.neurocomment.challenge_solver_enabled` (bool, default **`False`** — opt-in roll-out, mirrors #132's opt-in semantic-dedup pattern); `challenge_wait_timeout_seconds` (20.0); `challenge_gemini_timeout_seconds` (10.0); `challenge_min_confidence` (0.7, reserved for Phase-2 human-queue routing); `challenge_click_delay_min_seconds` / `_max_seconds` (3.0 / 6.0, `@model_validator` `min ≤ max`); `channel_challenge_backoff_min_failures` (3); `channel_challenge_backoff_base_seconds` / `_max_seconds` (3600 / 86400).

### Operator UX (React Neurocomment screen over `/api/v1/neurocomment`, built in #170)

The board surfaces the full solver-feedback loop:

- **Per-channel status badges** track the new enum (`chat_restricted` / `bot_challenge` / `bot_challenge_backoff`) with RU labels + icons.
- **Header counters** per campaign: `solved / failed / give_up / pending`, with a time-window toggle (Сегодня / 7 дней / Всё время) — one `GROUP BY outcome` over the audit table.
- **Drill-down** on a stuck channel: last 5–10 failed challenges with raw text + buttons + Gemini's `reasoning`. This is the in-place stand-in for a separate human-queue page — operators read the reasoning, decide, and act on the same row.
- **Manual actions per `(account, channel)`** in the drill-down: **Retry challenge** (re-runs solver, useful after a prompt/model tweak) and **Skip channel for this account** (operator override → `human_skipped`, engine ignores the pair).
- **Per-campaign Solver switch:** «Следовать настройке / ВКЛ / ВЫКЛ», maps to `neurocomment_campaigns.solver_enabled`. Lets the operator A/B a single campaign while the global flag stays off.

Phase-2 UI deferrals (deliberate): standalone human-queue page, "test challenge" debug form, time-series counter graphs.

### Why proactive on onboarding (ADR — see `decisions.md`)

The original Ф1 lazy-on-comment-failure model was a compromise driven by the absence of a solver. With a solver, proactive on onboarding is the only model that *can* work: guardian bots delete their prompt on timeout, so a lazy look-up at comment time finds nothing and a stale callback fails. Onboarding pays the wait only **once per `(account, channel)`**, parallelised across the campaign batch — cheaper than a per-comment cost would have been.

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

`services/neurocomment/board.py` → `load_neurocomment_board(campaign_id)` builds the work-view read model for the UI. Same invariant as warming: it **bulk-loads** accounts, campaign readiness, linked groups, today's posted comments, warming states, spam statuses, and fingerprints once — no per-card DB queries. Account health reuses the warming `evaluate_readiness` gate + `account_trust_score_from`; the only neurocomment-specific logic is the per-channel status derivation (`ready` / `comments_off` / `join_by_request` / `chat_restricted` / `bot_challenge` / `bot_challenge_backoff` / `throttled` — see the captcha section for the split).

The neurocomment UI is the React **Neurocomment** screen (`frontend/`, built in #170) over `/api/v1/neurocomment` (campaign create, channel pool, account picker, listener selection, Onboard/Start/Stop, board rendering). It carries no domain logic — everything is in `services/neurocomment/`, reached through the `api/` layer. (The old NiceGUI `features/neurocomment/` page was removed in the split-stack pivot.)

## Anti-ban knobs

All tunables live in `core/config.py` under `settings.neurocomment` (no magic numbers): `reply_delay_min/max_seconds`, `join_delay_min/max_seconds`, `max_comments_per_hour`, `max_comments_per_channel_per_day`, `comment_max_words`, `max_retries`, `peer_flood_cooldown_seconds`, `link_only_max_word_chars`, `stop_cancel_timeout_seconds`. `.env.example` must mirror these fields (`tests/test_architecture.py` enforces it).

## Operator-run (out of scope for code)

The live canary on real accounts and the ban-observation calibration are **operator-run** (human-in-the-loop): start with conservative `settings.neurocomment` defaults, run a small canary against real Telegram, watch for spam/ban signals, and tune the knobs. The code never auto-runs a real-account canary.

## Ф2 anti-detect (#131 deletion back-off built · #132 semantic dedup built · #120 challenge solver specced)

Three anti-detect guards, resolved via grill (see `context/decisions.md` → Ф2 update and the two 2026-06-24 entries for the solver). Deletion back-off (#131) and semantic dedup (#132) are merged to `main`; the challenge solver (#120) is fully designed and ready to implement (see the **Captcha — proactive challenge solver** section above).

- **Comment-deletion → channel back-off.** A periodic asyncio **sweep** (the lone non-event loop) re-reads recently-posted comments via a new gateway action `CheckMessagesAlive` (`get_messages(ids=[…])`→`None`, NOT the unreliable `MessageDeleted` event). Too many of a channel's comments gone within the window → trip an **escalating in-memory channel cooldown** (mirrors the per-account `_state` cooldown, recomputed each sweep, self-healing), and the engine stops selecting accounts for that channel until it expires.
- **Semantic dedup across accounts** (built — #132). Local **token-set Jaccard** over normalized text (no Gemini embeddings — hot-path latency), group+window scoped, plugged into the `_generate_acceptable` retry loop **after** exact-hash (`try_reserve_sent` stays the atomic claim); a candidate whose max similarity to the channel's recent **posted** comments in the window reaches `semantic_dedup_threshold` is released + regenerated. Threshold `0` disables it (the default — opt-in).
  - **Tuning caveat — in-flight race.** The comparison set is *posted* comments only, so two accounts commenting on *different* posts in the same channel within the reply-delay window can both post near-duplicates (neither is posted yet when the other generates). The per-post atomic claim only dedups the *same* post; cross-account semantic dedup is best-effort by design.
  - **Tuning caveat — short comments.** Token-set Jaccard ignores word order and frequency (good — it catches reordered/repeated paraphrases), but a 1–2 word comment matching any recent short comment scores `1.0` → rejected. An aggressive threshold therefore raises regeneration churn (→ more `failed` posts) on short-reply channels; push the threshold toward `1.0` (= block near-verbatim only) if that bites.
- New knobs (to land with the code, mirrored in `.env.example`): `deletion_sweep_interval_seconds`, `deletion_sweep_lookback_hours`, `channel_backoff_min_deletions`, `channel_backoff_base_seconds`, `channel_backoff_max_seconds`, `semantic_dedup_threshold`, `semantic_dedup_window_hours`.

## What does NOT belong here

- No direct Telethon/SQLAlchemy in `services/` or `api/` — go through `core.telegram_client` / `core/repositories`.
- No business logic in the `api/v1/neurocomment` routes or the React Neurocomment screen — they delegate to `services/neurocomment/`.
- No DB/Telegram access from `api/` — only through `services/neurocomment/`.
- No captcha solving inside `core/` — the gateway exposes `WaitForBotChallenge` + `ClickButton` only; all challenge orchestration / Gemini wiring / cache lookup lives in `services/neurocomment/challenge.py`.
