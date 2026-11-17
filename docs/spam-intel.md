# Spam intel: knowing an account's spam status without interrogating @SpamBot

Status: **Phase 0 approved with changes on 2026-11-16 (decisions D1-D13 accepted as recommended, section 10). Stage 1 is implemented in the PR that carries this file; Stages 2 and 3 are not started.** Where Stage 1 differs from the first draft, the text says *(as built)*. Every claim below about the current code was re-read line by line on `main` at `1eeb0b03`; every number from production was read read-only on 2026-11-16. Items we could not verify are listed in section 11 and are not assumed anywhere in the design.

Why: today the only way Telebuba learns an account's spam status is to open a session, send `/start` to @SpamBot, take the first reply and leave. It never opens the dialog, never reads the answer, never waits for the rest of the answer, and many independent triggers do it (section 2). Telegram can see that shape, and the data (section 3) shows the shape is worse than the volume: bursts of probes a few seconds apart across accounts, probes of accounts with no proxy, probes of a revoked session, and several forced probes for one account within minutes.

Goals, in the order they are won:

1. Never ask in a bot-like way: no bursts, no repeats, no probes of accounts that cannot answer, one shared budget (Stage 1).
2. Know more without asking: record the signals the accounts already produce during normal work, fold them into one verdict (Stage 2).
3. When asking is unavoidable, ask like a person, inside a session that is already alive (Stage 3).
4. Break nothing: each consumer keeps its contract, and every difference is named in section 6 and has a test (section 8).

A note on expectations, because the owner's brief says "most of the time without asking": production asks about **2.7 times a day for 15 accounts** (38 probes in 14 days), about once per account per 3 days. Passive signals cannot replace that routine probe for a healthy account, because nothing a healthy account does proves it is not limited (see section 4: the only documented proof is delivering a first message to a stranger). What passive signals do win: not asking after a PeerFlood that already told us, not asking an account we know is frozen or dead, serving the operator and the consumers from what is already known, and stretching the routine interval for accounts with a long clean record. The larger win is Stage 1 (bursts, duplicates, forced probes) and Stage 3 (the human shape). The design does not pretend otherwise.

---

## 1. Layers and the gateway

`api → services → core`, external I/O only in `core/`.

- `services/spam_intel/` is the **only** entry for "what is this account's spam status" and "may this account do X". Nothing else imports `core.telegram_client.check_spam_status`, calls `upsert_spam_status`, or reads the verdict through `services.spam_status.refresh_spam_status`. An AST test in `tests/test_architecture.py` (same import-scan style as the layer tests) fails the build on any such import (section 8, T1).
- `core/` keeps only I/O: the probe conversation (Stage 3 shape), the repositories, and a **signal port**: `core/telegram_client/_signals.py` holds a registered async callback; the gateway modules emit raw observations (`peer_flood`, `user_banned_in_channel`, `restricted`, `freeze_seen`) into it. `core` imports nothing from `services`; `services` registers the sink at startup. This mirrors the existing `fire_rebuild_hooks` registry in `_pool.py`. All randomness (pauses, jitter) is drawn in `services` and passed down as a model.
- `services/spam_status.py` stays as a **compatibility shim** for Stage 1 and 2: the same function names and signatures (`refresh_spam_status(account_id, *, force=False)`, `classify_spam_probe`, `lift_on_clean` wiring), delegating to the gateway. The three domain seams (`services/warming/_seams.py:187`, `services/neurocomment/_seams.py:255`, `services/chat_broadcast/_seams.py:127`) keep their names, so the tests that monkeypatch them (46 files) keep their patch points. The shim is deleted only in a later cleanup, once the architecture test has been green for a release.

### Public surface (sketch)

```python
class Purpose(StrEnum):      # why a verdict is wanted; drives the budget class
    ROUTINE = "routine"      # scheduled freshness (warming cycle, onboarding, outreach readers)
    DECISION = "decision"    # gates a risky or irreversible step (hand-off, ban confirmation, broadcast rest end)
    MEASUREMENT = "measurement"  # survival windows: a real @SpamBot answer is the product
    OPERATOR = "operator"    # the Spam-check button

async def verdict_for(account_id, *, max_age) -> Verdict            # DB only, never Telegram
async def verdicts_for(account_ids, *, max_age) -> dict[str, Verdict]   # bulk, for readers
async def ensure_verdict(account_id, *, purpose, caller, max_age, holder=None) -> VerdictAnswer
async def may_perform(account_id, action: RiskyAction) -> Permission
async def operator_check(account_id, *, confirm_out_of_turn=False) -> OperatorAnswer
async def record_signal(signal: SpamSignal) -> None                 # passive evidence, never raises
```

`VerdictAnswer` carries the `Verdict` and **how it was served**: `served_from` is one of `evidence`, `spambot_reading`, `probe`, `coalesced`, `deferred`, `refused`, with `reason` and, for `deferred`, `retry_at`. This is what lets a caller tell "unreadable because the probe failed" from "not asked because the budget said later" (section 6 shows why the difference matters: warming's streak, quarantine's unreadable counter and broadcast's permanent halt all key off `unknown`).

`caller` is a short label (`warming.cycle`, `warming.quarantine`, `warming.handoff`, `survival.window`, `survival.block_recheck`, `neurocomment.onboarding`, `neurocomment.ban_confirm`, `neurocomment.ban_budget`, `neurocomment.check_channels`, `chat_broadcast.banned`, `chat_broadcast.rest_end`, `api.operator`). It goes into the attempt row and the events, so the next audit does not have to infer the cause from timestamps, which is what this one had to do (section 3).

## 2. Inventory (verified against `main` @ `1eeb0b03`)

Every site that can send a message to @SpamBot, or that reads or writes the stored verdict. The only function that talks to @SpamBot is `check_spam_status` (`core/telegram_client/_spam.py:26`); the only importer of it is `services/spam_status.py:17`. The choke point already exists, which is what makes the gateway cheap to enforce.

### 2.1 Writers and probers

| Site (file:line) | Condition | Frequency / settings (default) | Mode | Does with the result | Tests |
|---|---|---|---|---|---|
| `services/warming/_spam_check.py:131-171` `_spam_step`, called from `_iteration.py:91-105` `_gate_spam` at the start of every cycle | `spam_check_due` (`:57-79`): no cached reading, cached `unknown`, `spam_unknown_streak>0`, first cycle of a stint whose reading predates it and is older than the TTL, or reading older than the interval | interval `spam_check_interval_hours=72` + a fixed per-account offset within ±`spam_check_jitter_hours=12` (`core/_config_warming_safety.py:22-23`); **every cycle** while the cache is `unknown` or the streak is above 0 | `force=True` only when the cache is `unknown`, else cache-respecting (TTL 36 h, `:17`) | `limited` and block not known → quarantine (`_quarantine_limited` `:93-113`); `unknown` → streak+1, cycle goes on | `tests/services/warming/test_spam_check.py`, `test_cycle_db_budget.py`, `test_loop_quarantine.py` |
| `services/warming/_quarantine.py:168` `_recover_from_quarantine` | quarantine window elapsed | after `quarantine_hours=48`; `quarantine_max_repeats=1`, `quarantine_unreadable_max=3` | **forced** | `clean` → release; still `limited` → `stop_spam_blocked` (parks in `spam_blocked`); `unknown` → extend, and after 3 unreadable windows park in `error` | `test_quarantine_generation.py`, `tests/api/test_warming_spam_block.py`, `test_spam_block.py` |
| `services/warming/_transitions.py:230-233` | cycle result `peer_flood` | enters quarantine for 48 h; records `quarantined` | no probe at entry | the probe comes 48 h later (row above) | `test_loop_quarantine.py` |
| `services/warming/_handoff_spam.py:89-107` `assert_spam_clean_for_handoff` via `_spam_check.py:82-90` `ask_spambot` | operator hands an account from warming to neurocomment | per click | cache first (≤ TTL), forced only for a cached `unknown` | `clean`, or `limited` with the block already known → proceed; `limited` → back to quarantine + refusal `spam_limited`; anything else → refusal `spam_check_unavailable` (409) | `test_handoff_spam.py` |
| `services/survival/_probe_loop.py:180-204` `run_probe_pass` (window probe) and `:160-177` `_recheck_spam_block` | hourly pass asks about at most one account: day 7 / day 30 window closed with no answer since; or a confirmed spam block whose last answer is old | `probe_interval_seconds=3600`, `probe_proxy_spacing_seconds=3600`, `probe_retry_hours=24`, `block_recheck_hours=72` (`core/_config_survival.py`) | cache-respecting | `spambot_verdict` journal row → the survival counter; a `clean` lifts the block | `tests/services/survival/test_probe_loop.py`, `test_due.py`, `test_block_recheck.py` |
| `services/neurocomment/onboarding.py:234-269` `_probe_account_spam` | neurocomment Start, once per serving account | per Start; pause `join_delay_min/max_seconds` = 30–120 s (`core/_config_domains.py:73-74`) only when a real probe went out | `force=False` | failure logged, never fatal | `test_onboarding_campaign.py` |
| `services/neurocomment/bans.py:173` `confirm_group_ban_and_leave` | refusal `UserBannedInChannelError` AND the group's own record says `restricted` | **per account, per channel**; the "check channels" button runs it for every restricted pair (`:91-92`) | **forced** | a non-`clean` reading means "the account is limited, the group is not at fault" → no sticky ban | `test_group_ban_confirmation.py`, `test_neurocomment_bans.py` |
| `bans.py:272` `register_unconfirmed_ban` | refusal with record `can_send`, and the pair's interval allows counting | per refusal | cache-respecting (but a miss opens a real dialogue on the post path, as its docstring says) | `clean` → count the refusal | `test_repeated_unconfirmed_ban.py`, `test_unconfirmed_ban_spam_recheck.py` |
| `bans.py:327` `_ban_on_a_spent_budget` | second counted refusal | per pair, once | **forced** (irreversible step) | `clean` → sticky ban and leave | same |
| `services/chat_broadcast/_seams.py:127-138` `spam_verdict`, callers `_rotation.py:217` and `:306` | a refusal `UserBannedInChannel` (`:217`); the end of a rest whose reason is `peer_flood` or banned-in-chat (`:306`, rest `spam_rest_seconds=21600`) | per refusal; per rest end | **always forced** | `:217` `clean` → only the chat moves; `:306` **anything but `clean` halts the account for good** (`halt_account(..., "spam_block")`, a final halt reason, see `_recovery.py:10`) | `test_refusals.py`, `test_recovery.py`, `test_engine_send.py` |
| `api/v1/accounts.py:145-160` `POST /accounts/{id}/spam-check` | operator clicks Spam-check in `WarmDaysModal.tsx:120` or `account-edit/SignalsSection.tsx:29` | unlimited | **forced** | returns the verdict; `lift_on_clean` runs | `tests/api/test_accounts.py`, `AccountEdit.test.tsx`, `WarmDaysModal.test.tsx` |
| `services/masstagging/_worker.py:298-306` | a masstagging halt with verdict `spamblock` | per halt | **writes `limited` itself** with `checked_at=now`, no journal row, no lift hook | the stored `limited` then looks like a fresh reading for 36 h | `tests/services/masstagging/test_engine_failures.py` |
| `services/spam_status.py:181-245` `refresh_spam_status` | the single funnel for all rows above | — | per-account `asyncio.Lock` | probe → classify → `upsert_spam_status` → `log_event` → `spambot_verdict` journal row → `lift_on_clean` | `tests/services/test_spam_status.py`, `tests/core/test_spam_status_probe.py` |

### 2.2 Readers (no Telegram traffic)

| Site | What it reads | What it does |
|---|---|---|
| `services/accounts/_outreach_protection.py:69,85-88` | stored status of the run's accounts | skips an account whose stored status is `limited` (`account_spam_blocked`), **whatever its age** |
| `services/masstagging/_fleet.py:54-62` | stored status | `limited` → account unusable for the run |
| `services/trust.py:84,211` | stored status + `spam_block_known` | `limited` docks the trust score by `penalty_spam_limited=50` unless the block is already known; `unknown` docks 0 |
| `services/neurocomment/engine.py:264` and the `_gates` ladder | stored status in the selection pool | never probes (comment `:316-318`) |
| `services/warming/_transitions.py:124`, `_snapshot.py:56`, `_runtime.py:262`, `pacing.py:136` | stored status | readiness and intensity |
| `services/accounts/_table.py:142-163` | all stored statuses | `AccountRead.spam_status` / `spam_detail` for the UI |
| `services/spam_block_lift.py:47-92`, `schemas/_warming_states.py:64-86` | stored verdict vs `spam_blocked_at` | a `clean` newer than the block clears the mark; `lift_from_stored_answer` re-applies it from the survival pass |

### 2.3 Passive signals, and what they do today

| Signal | Where it is raised | Today |
|---|---|---|
| `PeerFloodError` on a gateway action | `core/telegram_client/_actions.py:138-141` → `ActionResult(status="peer_flood")` | each domain reacts alone: warming quarantines, broadcast rests, masstagging halts, neurocomment cools down; **nothing updates the spam verdict** (masstagging excepted, above) |
| `PeerFloodError` on a read | `_read.py:181-185` → `TelegramReadError(kind="flood_wait")` with reason `PeerFlood` | indistinguishable from a flood wait except by string (`services/accounts/contact_lookup.py:249` matches on the reason text) |
| `PeerFloodError` in the chat gateway | `_chat_writes.py:71-77`, `_chat_common.py:146-149` re-raise; `:134-139` maps `UserBannedInChannel` **and** `ChatWriteForbidden` **and** `UserPrivacyRestricted` to one code `chat_write_forbidden` | the operator inbox loses the distinction |
| `UserBannedInChannelError` | `_actions` result `error_type`; `_listener.py:89` treats it as lost access | neurocomment confirms through `GetParticipant` + @SpamBot (`bans.py`); broadcast asks @SpamBot |
| `get_me().restricted` | read inside the probe (`_spam.py:111-119`) and by every session check | used only if no text marker matched. Telegram documents `restricted` as "access to this user must be restricted for the reason in `restriction_reason`" — **not** a spam-limit flag (section 4) |
| `help.getAppConfig` `freeze_*` | `_session.py:217-251`, inside the session check only; `FROZEN_METHOD_INVALID` in `_actions.py:147-151` marks the account `frozen` | writes the account status, not the spam verdict |
| `FloodWait` | everywhere | a rate limit, not a spam verdict; **not evidence** (section 5.3) |

### 2.4 The starting map (spec section 1): confirmed, corrected, new

| Claim | Result |
|---|---|
| Probe at `_spam.py:92-119`, one `/start`, first reply, then `get_me` | **Confirmed, with two additions.** The function is `check_spam_status` `:26-72` plus `_probe_spambot` `:75-108`. It takes the **first** `NewMessage` only (a two-message answer is truncated), and never marks the dialog read, so the bot's answer stays unread on the account for ever. |
| Parser `spam_status.py:30-165`, unrecognised text → `clean` | **Confirmed**: `return "clean", None` at `:140`. **The "de" claim is false**: `_classify_reply_text` and `classify_spam_probe` document en/ru/de, but no German marker exists. A reply in any language without markers is `clean` unless it carries a date. |
| Statuses only `clean`/`limited`/`unknown` | Confirmed (`schemas/spam_status.py:17`). |
| Table `account_spam_status`, TTL 36 h, `spambot_verdict` event, errors not saved | Confirmed (`core/_schema_tables.py:280-287`; `_is_fresh` `:168-178`; `:240-242`; `_uncached_unknown` `:248-255`). The table is created by `create_all`; it has a foreign key to `accounts` **without** `ON DELETE CASCADE`, which is why `core/repositories/_accounts_delete.py:143-217` deletes it by hand. Every new table needs the same line (risk R1). |
| Masstagging writes `limited` itself (`_worker.py:298-306`) | Confirmed; and it bypasses the journal and the lift hook. |
| Lift on any `clean` (`spam_status.py:244`, `spam_block_lift.py`) | Confirmed: `lift_on_clean` after the upsert; the lift needs a `clean` read after `spam_blocked_at` (`lifts_spam_block`, `_warming_states.py:64-69`). |
| Migrations 79, 94, 96 | Confirmed (`core/migrations.py`). The next free number is **114**. |
| Warming: every 72 ± 12 h, but every cycle on `unknown`/streak | Confirmed. The per-account shift is a stable hash (`check_offset_hours` `:45-54`), not a draw. |
| Quarantine 48 h, forced | Confirmed (`_quarantine.py:168`). |
| Hand-off cache first | Confirmed (`ask_spambot` `:82-90`). |
| Button: forced, unlimited | Confirmed. It is reachable from two UI places. |
| Survival 7/30-day windows, retry 24 h, blocked 72 h, per-proxy spacing | Confirmed. **New:** the retry memory and the per-proxy spacing live in process memory (`_State`, `_probe_loop.py:62-70`); after a restart a probe that FAILED (no journal row) is retried at once once the 600 s startup delay passes. |
| Neurocomment start pause 30–120 s; ban confirmation often forced; "check channels" asks per pair | Confirmed: the pause is a jittered join delay and applies only after a real probe (`onboarding.py:268`). **New:** `refresh_spam_status`'s docstring says the per-account lock "collapses concurrent callers". That is true for cache-respecting callers only: a **forced** caller that waits on the lock then probes anyway (`:209-213`), so N forced callers on one account produce N probes. |
| Broadcast always forced; also on `UserBannedInChannel` and rest end | Confirmed. **New, and risky:** `_rotation.py:306` turns any non-`clean` answer (including a failed probe, which reads `unknown`) into a **final halt** of that account in the run. |
| Outreach protection only reads the cache | Confirmed, plus: it reads `limited` of any age (see 2.2). |
| No shared limit | Confirmed. |
| Prod facts (18 accounts, 14 clean/4 limited, 38 probes in 14 days on 13 accounts) | Confirmed (section 3). |

Other findings, not in the starting map:

- `_spam_check._gate_spam_check` (`:116-128`) has **no production caller**; the cycle uses `_iteration._gate_spam`. It is exercised only by `test_spam_check.py`.
- `_probe_self_restriction` reads `me.restricted`, which Telegram does not tie to spam limits (section 4), and the parser maps it to `limited` when no marker matched.
- There is **no caller attribution anywhere** in the stored data: neither the `logs` row nor the journal row says who asked.
- The pool never disconnects an idle client (`core/telegram_client/_pool.py:185-239`; no idle timer), so the first probe of an idle account opens a cold connection that then stays open. For a Stage 3 "session already alive" the gateway needs a small `core` helper that says whether a connected client exists (section 5.7).

## 3. Baseline

### 3.1 Code health before any change (this worktree, `main` @ `1eeb0b03`)

| Check | Result |
|---|---|
| `uv run ruff check .` | passed |
| `uv run ruff format --check .` | 1620 files already formatted |
| `uv run ty check .` | passed |
| `uv run pytest --maxfail=10 -n auto` (the CI command; 9716 items, 8 workers, 18 min) | **9710 passed, 6 skipped, 0 failed**; branch coverage 95.86 % (floor 90 %) |

Not run in Phase 0: `pre-commit`, `arch-guard`, `aislop`, `pip-audit`, `semgrep`, the frontend gates and the MEX check. They run in each stage's PR.

### 3.2 Production, read-only (2026-11-16, `telebuba.db` opened `mode=ro`, no Telegram traffic, no `.session` files opened; scripts copied by `scp` and deleted after each run; account ids hashed to 6 hex characters; nothing secret read or printed)

- 18 accounts: 17 `alive`, 1 `unauthorized`; 15 have a proxy, **3 do not**. Warming rows: 12 `idle`, 3 `sleeping`. Proxies carry 3 accounts each (5 proxies).
- Stored verdicts: 14 `clean`, 4 `limited` (3 of the 4 are the proxy-less accounts, limited since 2026-11-05 with no warming row; the fourth is idle, limited since 2026-11-03). **Nothing re-asks any of these four**: only warming cycles, the quarantine ladder and the block re-check ask, and none applies. Their `limited` therefore blocks outreach and masstagging for them indefinitely.
- Lifecycle journal: **99** `spambot_verdict` events since 2026-09-13 over 35 account ids (34 accounts since deleted): 69 `clean`, 30 `limited`, **0 `unknown`**. 38 of them fall in the last 14 days (6+1+7+2+7+2+7+1+1+2+2 per active day), on 13 accounts. Routine cadence is visible: one probe per warming account every 72 h (e.g. 11-02, 11-05, 11-08, 11-11, 11-14).
- Detail of the 30 `limited`: 25 with no detail (no date, so a marker matched but no `until` or date), 5 with `29 Oct 2026` (found by the date regex, not by an `until` tail). **We never kept the reply text**, so the parser cannot be audited against reality.
- **Bursts** (gaps between consecutive verdicts under two minutes): 28. The worst: 11 probes across 11 accounts in 84 s on 2026-09-17 (gaps 6-10 s); 5 in 21 s on 10-27 05:52 (just before two `warming_started` rows); 3 in 8 s on 11-02 19:04, each at the start of that account's warming cycle (the verdict lands within a second of its `warming_telegram_set_online` or `warming_day_counted`); 3 in 7 s on 11-05 10:08, 63 s after three new accounts were added and audited, **with no proxy**. Warming's `startup_jitter_max_seconds=8.0` (`core/_config_warming.py:62`) is consistent with the 11-02 and 10-27 shapes: a restart makes every loop due at once. For the 11-05 shape the likely cause is three operator clicks. Neither is provable, because nothing records the caller.
- Errors since 2026-10-18 (the `logs` horizon): 70 `spam_status_refreshed`, 6 `telegram_spam_status_probe_failed` (**5 `SessionRevokedError`**, 1 `TelegramClientPoolError`), 4 `warming_spam_check_unreadable`, 1 `warming_spam_check_limited`. A probe of an account whose session is revoked cannot succeed and should never be attempted.
- No `unknown` is ever stored, but the log shows 6 failed probes: failures are not persisted, so every caller retries on its own schedule.
- **Language is the large finding.** Device-fingerprint `lang_code` of the 18 accounts: `es` 7, `en` 5, `pt` 3, `ru` 2, `fr` 1. @SpamBot answers in the client's language (spec; we could not confirm it, section 11). The parser has markers for en and ru only. By language, the stored status is: `en` 1 clean / **4 limited**; `es` 7 clean; `pt` 3 clean; `fr` 1 clean; `ru` 2 clean. All 41 journal verdicts of the es/pt/fr accounts still present (27 + 13 + 1) are `clean`; **all 4 `limited` accounts are English**. Either those accounts really are clean, or the parser reads their limits as `clean` through the fallback at `spam_status.py:140` (a Spanish or Portuguese limit with no date has no marker and no date → `clean`). **Current data cannot tell which.** 11 of the 14 `clean` verdicts rest on that fallback. This is the strongest reason for the strict parser and for keeping reply samples, and it is also the main rollout hazard of the strict parser (R3).

## 4. What Telegram says (sources; forum lore is marked as unverified)

Fetched 2026-11-16 from core.telegram.org and telegram.org. Quotes are short; "undocumented" means the page does not say.

- **`PEER_FLOOD`** — [errors.json](https://core.telegram.org/api/errors.json): "The current account is spamreported, you cannot execute this action, check @spambot for more info." Code 400, no method list ("may be empty for errors that can be emitted by any method"). Not in the `messages.sendMessage` / `forwardMessages` tables. Whether it is account-wide: the text says "the current account"; nothing else.
- **`USER_RESTRICTED`** — "You're spamreported, you can't create channels or chats." Methods `channels.createChannel`, `messages.createChat`, `channels.editAdmin` (codes 403/406). It is about creation, not messaging.
- **`USER_BANNED_IN_CHANNEL`** — [messages.sendMessage](https://core.telegram.org/method/messages.sendMessage): "You're banned from sending messages in supergroups/channels." The wording is not "in this chat". **The docs do not say whether it is per chat or account-wide.** The repository's neurocomment code already treats it as account-wide and uses a participant read to separate the two (`bans.py:126-143`); that is empirical, not documented. `CHAT_WRITE_FORBIDDEN` ("You can't write in this chat") and `SLOWMODE_WAIT_X` are chat-specific by their wording.
- **`restricted` / `restriction_reason`** on `user` — [constructor/user](https://core.telegram.org/constructor/user): "Access to this user must be restricted for the reason specified in `restriction_reason`." The linked type is `RestrictionReason(platform, reason, text)`. **Nothing ties it to spam limits or @SpamBot.** There is no documented method that returns "spam limited" for the current account. So `restricted` is a platform/legal restriction signal, not the thing @SpamBot reports, and must not be read as a spam limit with the weight the parser gives it today.
- **Freeze** — [api/auth, Frozen accounts](https://core.telegram.org/api/auth): "Frozen accounts are in read-only mode"; `FROZEN_METHOD_INVALID` (420, listed for `channels.deleteMessages`, `channels.joinChannel`, `channels.searchPosts`) and `FROZEN_PARTICIPANT_MISSING` (400); "clients should invoke `help.getAppConfig`" on the first. `freeze_since_date`: "indicates when was the account frozen"; `freeze_until_date`: "indicates when will the account be **deleted**, unless an appeal is submitted and accepted"; `freeze_appeal_url`. So `freeze_until_date` is a **deletion deadline, not the end of the freeze**; it must not be used as an expiry of the evidence. Whether a frozen account can talk to @SpamBot: undocumented.
- **What a limited account can still do** — the official [spam FAQ](https://telegram.org/faq_spam?setln=en): "Limited accounts can send messages to people who have their number saved as a contact", and you can "always reply to anyone who messages you first". Limits are "temporary" and, for a first offence, "a few days or so"; "repeated offences will result in longer periods"; one may lose the ability "forever". Early release is "impossible". Appeal: contact @SpamBot. **Not stated:** whether a limited account can post in groups it already belongs to, join groups, write to mutual contacts, or add contacts. The FAQ gives no sample of @SpamBot's text, no buttons, no languages, and does not mention PEER_FLOOD.
- **What this means for "proof of clean".** The only documented limitation that a limited account cannot get round is **writing first to a stranger** (not a contact, who has not written to it). A successful first-contact DM to such a person therefore **is** documented evidence that the account is not limited right now. Everything else a healthy account does (group posts, joins, reads, replies, DMs between our own warming accounts, which are contacts or repliers) is **not** documented to be blocked by a limit, so success there proves nothing. The design gives those weight 0 until live evidence says otherwise (section 5.3).
- **@SpamBot reply variants** (clean; limited until a date; limited with no date; under review; appeal flow; several messages; buttons; language per client `lang_code`): **no official source.** Everything we know of its text comes from our own parser comments and memory, not from a verified capture. Third-party guides (unverified) say the bot may keep saying "no limits" while some limits are still being removed, with a delay "up to 24 hours". The design therefore (a) stores bounded, redacted reply samples from the first Stage 1 deploy, (b) builds fixtures only from captured texts, and (c) treats unrecognised text as `unknown`, never `clean`.

## 5. Design

### 5.1 Principles

1. Evidence is a fact with a timestamp and a source; **weights and half-lives are policy in code**, not stored in rows. Changing a weight re-folds history deterministically and needs no migration. (This deviates from the spec's "Evidence has `weight`": a stored weight would freeze yesterday's policy into the data. The fold output still reports the weight used.)
2. The verdict is a pure function of the evidence and the clock. It is stored as a projection (`account_spam_status`), recomputed on write, never on read.
3. `unknown` is never `clean`. A consumer asks "may I do Y", and `unknown` answers "not proven".
4. The budget is data in the database, not memory.
5. Every stage ships dark behind a switch (5.11), with the old path intact, until the owner has seen it in production.

### 5.2 Request classes and what each may do

| Purpose | Typical caller | May serve from evidence/cache | May probe @SpamBot when | Waits? |
|---|---|---|---|---|
| ROUTINE | warming cycle, neurocomment onboarding | yes, if confidence is high and fresh enough | the verdict is stale **and** no limiting evidence explains the state | never blocks: `deferred` with `retry_at` |
| DECISION | hand-off, ban confirmation, broadcast rest end, quarantine recheck | yes, when fresh | the evidence conflicts or is stale **and** the step is risky | returns `deferred`/`refused`; the caller keeps its safe default |
| MEASUREMENT | survival windows | no: a real answer is the product | on schedule, budget permitting | `deferred` |
| OPERATOR | the Spam-check button | shows what is known first | the operator asks; hard minimum 30 min; confirmation when the last probe is under 24 h | answers immediately with a reason if not allowed |

### 5.3 Evidence kinds and weights

`strength(t) = base × 0.5^((t − observed_at)/half_life)`, and 0 after `expires_at`. Direction is `limited`, `clean`, `frozen` or none.

| Kind | Source (raised where) | Direction | Base | Half-life | Expires | Notes |
|---|---|---|---|---|---|---|
| `spambot_limited` | the @SpamBot reply | limited | 1.0 | none | the date in the reply, +1 h; none if undated | an undated limit never expires by itself, it goes `needs_recheck` after `limited_recheck_days` (5) |
| `spambot_clean` | the @SpamBot reply | clean | 1.0 | 120 h | none | "clean ages": below strength 0.5 the verdict is flagged stale |
| `spambot_unreadable` | reply with no recognised structure | none | 0 | — | — | kept as a sample; verdict `unknown` |
| `spambot_reviewing` | "being checked" text | none | 0 | — | 6 h | verdict `unknown`, shorter backoff |
| `peer_flood_send` | `execute` result `peer_flood` on a send-family action | limited | 0.7 | 72 h | none | the documented meaning is "spamreported"; it still does not say the bot will confirm |
| `peer_flood_other` | `peer_flood` on a join/read/other action | limited | 0.4 | 48 h | none | weaker: not shown to be a spam verdict for these methods |
| `user_banned_in_channel` | a send refusal | limited | 0.5 | 72 h | none | scope (account vs. chat) is undocumented; neurocomment's participant read keeps deciding the chat question |
| `tos_restricted` | `get_me().restricted` | none | **0** | none | until `restricted` is seen false | *(changed after review)* Telegram does not tie `restricted` to spam limits, so it never moves the spam verdict. It is kept as a separate flag with its reason and shown beside the verdict, so a platform restriction is visible without being read as a spam limit (the mistake the legacy parser makes today) |
| `freeze_seen` | `freeze_since_date` ≠ 0, or `FROZEN_*` | frozen | 1.0 | none | until `freeze_cleared` | `freeze_until_date` is stored as detail only (it is a deletion deadline) |
| `freeze_cleared` | appConfig with no freeze after a `freeze_seen` | clean (freeze only) | 1.0 | — | — | closes `freeze_seen` |
| `dm_stranger_ok` | a delivered first-contact DM to a non-contact who had not written first (outreach/bulk path; **the caller must assert "stranger"**) | clean | 0.8 | 48 h | none | the one documented proof (section 4) |
| `group_send_ok`, `join_ok`, `read_ok`, `dm_known_peer_ok` | normal work | clean | **0 (shadow)** | — | — | recorded, weight 0, so Stage 2 can measure their agreement with @SpamBot before any weight is assigned |
| `flood_wait` | rate limits | none | 0 | — | — | not evidence of a spam limit |

### 5.4 The fold (pure, deterministic)

`fold(evidence, now, cfg) -> Verdict`:

1. A non-cleared `freeze_seen` → `frozen`.
2. Let `L` be the strongest unexpired limited evidence and `C` the strongest unexpired clean evidence (by `strength(now)`).
3. **Supersede rule.** A clean item supersedes a limited item if it is **newer** and its **base is not lower**. So a newer @SpamBot clean supersedes any older limited; a newer `dm_stranger_ok` (0.8) supersedes `peer_flood_*` and `user_banned_in_channel`, but does **not** supersede an older `spambot_limited` (1.0).
4. If an unsuperseded limited exists → `limited`. If a conflict exists (a newer clean that does not supersede) the verdict is still `limited` (the safe side) with `contradiction=true` and `needs_probe=true`: **this is the only state in which a DECISION request is allowed to ask @SpamBot because of evidence**, together with staleness.
5. Else if `C` exists → `clean`, confidence from `C`.
6. Else `unknown`.
7. Confidence = strength of the deciding item, bucketed (≥ 0.75 high, ≥ 0.4 medium, else low). `valid_until` is the expiry of the deciding limited item.
8. A dated limit that has expired contributes 0, so the verdict becomes `unknown` with reason `limit_expired` and `needs_probe=true`. This is what removes the stuck-limited state seen on production (section 3.2).
9. Output also carries `primary_kind`, `last_spambot_at`, `next_probe_allowed_at` (from the budget) and `weights_used`.

Properties covered by `hypothesis` (T4): order independence; idempotence; adding evidence older than everything and weaker than the winner never changes the status; a newer `spambot_clean` never leaves `limited` standing; `clean` is never produced without clean evidence of strength > 0; confidence is monotonic non-increasing in `now` for fixed evidence; `needs_probe` never true for a verdict with high confidence and no contradiction; frozen is never overridden by clean evidence of any kind except `freeze_cleared`.

### 5.5 Budget and scheduler

State lives in the database (survives restart). One decision function plus one in-process single-flight map per account.

Stored: `spam_probe_attempts` (every real probe: account, proxy key, `started_at`, `finished_at`, `caller`, `purpose`, `outcome`, `error_type`; row written at **start** so a crash still counts) and `spam_probe_state` (per account: last attempt, last success, fail streak, `next_allowed_at`, last caller, last reason). All gaps are measured from these rows, never from memory.

Rules, evaluated in this order; each refusal is an event `spam_intel_probe_refused` with the reason:

1. `account_missing`, or account status other than `alive` (revoked, unauthorized, frozen, deactivated): refuse. (Production: 5 probes of a revoked session.)
2. **No proxy → refuse, always.** (The spec's rule; D1.)
3. **Single-flight and coalescing:** concurrent callers for one account, **forced or not**, await the one in-flight probe. A real reading younger than `coalesce_minutes` (30) is returned to everyone, forced or not; so is a failed attempt younger than that (it returns the failure, it does not repeat).
4. **Hard minimum** *(as built)*: nobody, the operator included, gets a probe within 30 minutes of the previous one (rule 3 makes that unreachable by design). The draft's extra 6 h minimum for decisions is replaced by `account_daily_cap` (4 real probes per account per rolling 24 h, every purpose but the operator's): a 6 h minimum would have postponed a broadcast's chat-ban check for hours, where the cap bounds the same overuse without changing what a decision means.
5. **Routine interval**: *(deferred to Stage 2)* the adaptive 72 → 120 → 168 h stretch (D2) needs the "no limiting evidence" signal; Stage 1 keeps warming's own 72 ± 12 h cadence and the stable per-account offset.
6. **Backoff after `unknown` or failure:** 6 → 12 → 24 → 48 h (cap), reset by a readable reading. Applies to ROUTINE, DECISION, MEASUREMENT; OPERATOR passes it only with confirmation.
7. **Per-proxy spacing:** no two real probes behind the same proxy within `proxy_spacing_minutes` (120), from the attempts table.
8. **Fleet spacing:** between any two real probes anywhere, a jittered gap of 3-10 minutes (the jitter drawn in `services`), for ROUTINE/DECISION/MEASUREMENT. The 11-in-84-seconds shape cannot recur. OPERATOR is exempt (one human click).
9. **Fleet daily caps:** soft cap 10 per rolling 24 h (ROUTINE and MEASUREMENT are deferred past it; DECISION and OPERATOR proceed with a `spam_intel_budget_exceeded` warning event); hard cap 24 (everything refused, operator included). Production's busiest day was 12, the burst.
10. **Operator extra probes:** at most `operator_daily_cap` (3) per account per day, and a click within `out_of_turn_hours` (24) of the last probe needs `confirm_out_of_turn` *(as built: the button's route confirms for the operator until the confirmation dialog lands with the card in Stage 3, so the cap and the 30-minute minimum are what bound it now)*.
11. **Busy or lent account:** if `owner_of(account_id)` is another feature, or `is_lent(account_id)`, a caller that is not that owner is deferred until the account is free. A caller that **is** the owner passes `holder=` and proceeds: warming's cycle, the broadcast run and masstagging ask for their own accounts. `services._account_owner` is read, not changed.
12. **Quiet start:** for `startup_quiet_minutes` (10) after process start, ROUTINE is deferred (the restart storm).

The result is `deferred(retry_at)` or `refused(reason)`, never a fabricated `unknown`.

### 5.6 Stored projection and migrations (all additive)

- **Stage 1, migration 114** `add_spam_probe_ledger`: tables `spam_probe_attempts` (indexed by account/time and proxy/time; retention 90 days or 5000 rows), `spam_probe_state`, `spam_reply_samples` (redacted, deduplicated by hash of normalised text; ≤ 1000 characters; ≤ 200 rows; fields: hash, sample, first/last seen, count, language, structural flags, classification). Backfills `spam_probe_attempts` from the last 14 days of `spambot_verdict` journal rows so the budget does not start empty. Each table gets a delete line in `core/repositories/_accounts_delete.py`.
- **Stage 2, migration 115** `add_spam_evidence`: table `account_spam_evidence` (account, kind, direction, observed_at, expires_at, source, operation, detail ≤ 200 characters, count, last_seen_at; coalescing: the same account/kind/source within 10 minutes increments `count` instead of adding a row; retained 30 days or 50 per account) and additive nullable columns on `account_spam_status`: `confidence`, `valid_until`, `primary_kind`, `last_spambot_at`, `needs_probe`. Old readers keep reading `status`, `detail`, `checked_at` untouched.
- No change to the `account_lifecycle_events` CHECK (a new kind there means rebuilding the table, as migrations 95/96/105/106 did). `spambot_verdict` is written **only for real @SpamBot answers**, exactly as now, so the survival counter's meaning is unchanged; the extra fields (`caller`, `purpose`) ride in its existing JSON `extra`.
- Existing state that stays where it is: `spam_unknown_streak` (79) and `spam_blocked_at` (94) remain warming's; the lift (96) stays `services/spam_block_lift.py`, called by the gateway after a persisted **@SpamBot** `clean`. A passive `clean` never lifts a block (D4).
- Migration tests follow `tests/core/test_migrations_*.py`: fresh DB (`create_all` + all steps), a DB at the previous version, idempotent re-run, backfill, account-delete cleanup.

### 5.7 The probe as a person (Stage 3, `core/` = I/O only)

`core/telegram_client/_spam.py` gains `converse_with_spambot(client, plan: HumanProbePlan) -> SpambotTranscript`. `HumanProbePlan` is a Pydantic model of pauses drawn in `services` (`pre_open`, `open_to_typing`, `typing_seconds`, `reply_wait_max`, `quiet_after_last_reply`, `read_delay`). Sequence: resolve `@SpamBot` (a first resolution is natural, the entity is then cached) → open the dialog (`messages.getHistory`/`getPeerDialogs` for the bot) → pause → `setTyping` → pause → `/start` → **collect every message until `quiet_after_last_reply` seconds pass with none** → pause → `ReadHistory`. Handler registered **before** the send (the existing race fix is kept). **No button is pressed. The dialog is never deleted.** `get_me()` is no longer called by the probe (its `restricted` field is a different signal, taken where `get_me` already runs: the session check). The transcript carries `texts: list[str]`, `has_markup: bool`, `n_messages`, `elapsed`.

"Inside a live session": `core.telegram_client.has_live_client(account_id)` reports whether the pool already holds a connected, authorised client. The gateway then prefers that moment:

- the warming cycle asks **after** its presence flip and first reads, not before its first RPC as `_gate_spam` does today;
- the neurocomment listener holds its client for hours: ROUTINE/DECISION probes there are deferred to a jittered moment of that session instead of connecting;
- an account with no live client is probed only when the request is DECISION/OPERATOR/MEASUREMENT, and as a short ordinary session: open the app (read a few dialogs), then the sequence above.

Open point (section 11): whether the pooled client being "online" while connected is itself a tell; we do not change presence behaviour in this work.

### 5.8 The parser

`services/_spam_parse.py`, pure *(as built: beside `services/spam_status.py`, which runs the probe and imports it, because the package root imports the executor and a parser under the package would be a cycle)*. Order: (1) probe error → `unknown`; (2) a **structural** read independent of language: a date/time with a month name or numeric form, a multi-message transcript, markup present; (3) per-language lexicon with a language tag (`en`, `ru`, `de`, `es`, `pt`, `fa`, `ar`, `tr`, `uk`, `uz`, `id`, `fr` added because production has a French account); (4) anything else → `unknown` with `reason=unrecognized_reply` and a redacted sample stored. The "limited" lexicon is checked **before** the "clean" lexicon (today clean wins, which is why `ограничен` needed a special case), and a text that matches both is `unknown` with reason `ambiguous_reply`. Dates: parsed to an aware UTC datetime where the form allows (it becomes `expires_at`); unparsable dates are kept as text.

Redaction before storing: strip the account's own first/last name, username and phone digits (known from the account row), mask any run of 5+ digits, cap 1000 characters, store once per hash with a counter. No other personal data is retained.

**Language lexicons are not invented.** The current parser has en and ru; a German marker does not exist despite its docstring. For es, pt, fr (the languages of 11 of our 18 accounts) and the rest, the lexicon entries and their fixtures are added **only from captured samples** or from texts the owner confirms; until then those languages answer `unknown`. See R3 and D6 for how this is rolled out without parking healthy accounts.

### 5.9 Passive signals

`record_signal` is cheap and cannot fail a caller: it does one coalescing `INSERT/UPDATE` in `asyncio.to_thread`, swallows and logs any error. Sources:

- `core` emits raw observations through the port at: `_actions.py:138-141` (with the action family), `_read.py:181-185` (reason `PeerFlood`), the chat gateway PeerFlood and `UserBannedInChannel` arms (`_chat_writes.py`), `_session.py` (`me.restricted` and the freeze fields, where `get_me` and `getAppConfig` already run), and `FROZEN_*` in `_actions.py:147`.
- Services emit the ones only they can know: `dm_stranger_ok` from the outreach/bulk send path, which knows the recipient was a stranger; masstagging's `spamblock` becomes `peer_flood_send` evidence instead of a direct `upsert_spam_status`.
- **No new Telegram request is added for any signal.**
- `PeerFlood` on a read is evidence only as `peer_flood_other` (weak); the existing `kind="flood_wait"` mapping of the read gateway is kept for its callers.

### 5.10 Observability and UI

Events (`log_event`, no secrets, class names and codes only): `spam_intel_probe` (caller, purpose, reason, outcome, duration ms, proxy key hash), `spam_intel_probe_refused` (rule), `spam_intel_coalesced`, `spam_intel_budget_exceeded`, `spam_intel_evidence` (kind, direction), `spam_intel_verdict_changed` (from, to, primary kind), `spam_intel_parser_shadow_disagreement` (Stage 1 shadow, section 7), `spam_intel_sample_stored`. Counters derive from `spam_probe_attempts`.

Account card (Stage 3): verdict chip; confidence; source line ("@SpamBot, 3 days ago", "PeerFlood, 2 hours ago"); "limited until"; "next check possible …"; "checks in 7 days: N". The Spam-check button shows the age of the last probe; between 30 minutes and 24 hours it asks for confirmation ("out of turn"). A fleet-level warning on the Accounts page when the daily cap or the out-of-turn count is exceeded. API: additive fields on `AccountRead` (`spam_confidence`, `spam_source`, `spam_observed_at`, `spam_valid_until`, `spam_next_probe_at`, `spam_probes_7d`) and an additive result model for `POST /accounts/{id}/spam-check` carrying `served_from`, `next_allowed_at`, `probed`; `confirm_out_of_turn` is a request field. Frontend by the existing design system (`design/`, i18n ru/en), with Vitest next to the touched widgets (`AccountEdit.test.tsx`, `WarmDaysModal.test.tsx`, `SignalsSection.test.tsx`) and the Playwright spec `frontend/e2e/account-editor.spec.ts` where it already covers the editor. `uv run python -m tools.gen_api` regenerates the types in the same PR.

### 5.11 Switches (rollback without a deploy)

New `core/_config_spam_intel.py` (`SPAM_INTEL__*`, mirrored in `.env.example`, which `tests/test_architecture.py` already checks). A change takes effect after a restart, which on the production machine is routine (it pulls `main` on launch); nothing needs a deploy. Every value is validated (ranges, `min ≤ max`).

| Switch | Stage | Default at merge | Off means |
|---|---|---|---|
| `enabled` | 1 | on | *(as built: one switch for the gateway and its budget)* every caller reaches @SpamBot exactly as before: the legacy cache/force function, no ledger, no budget |
| `history_sweep` | 1 | **off** | *(new)* the read-only pass over each account's old @SpamBot dialog never runs; it needs the owner's explicit go |
| `strict_parser` | 1 | **off** | the legacy heuristic (unrecognised → `clean`) |
| `parser_shadow` | 1 | on | logs where strict would have differed |
| `sample_capture` | 1 | on | no reply samples stored |
| `passive_signals` | 2 | on | no evidence is recorded |
| `passive_decisions` | 2 | **off** | the fold runs and is shown but does not suppress or trigger any probe (shadow) |
| `human_sequence` | 3 | off until a live check | the single `/start` probe as today |
| `live_session_embedding` | 3 | off until a live check | probes at the caller's moment |

Per-stage thresholds (all validated, `core/_config_spam_intel.py`): coalesce 30 min; account daily cap 4; proxy spacing 120 min; fleet gap 3-10 min; soft/hard caps 10/24; operator daily cap 3; startup quiet 10 min; routine 72/120/168 h; backoff 6/12/24/48 h; limited recheck 5 d.

### 5.12 Semi-automatic appeal of a spam block (Stage 3; added at the owner's request, 2026-11-16)

**Hard limit, first.** The program never presses an @SpamBot button ("This is a mistake" and the like), never answers one of its questions, never passes or solves the appeal's captcha, never sends appeal text. A human does the whole appeal in the account's own Telegram Web window. The only message the code ever sends to @SpamBot stays the `/start` of the probe, already agreed. What the code adds is a notification, a window opened on the right dialog, and a record that the human says they filed.

1. **Notification when block evidence appears** (a `limited` @SpamBot reading, or fresh PeerFlood evidence), through the existing operator bot (`services/_notification_messages.py`): the account, the end date or "бессрочно", and the source ("@SpamBot", "PeerFlood"). The date and the source are values the program derived (an ISO date, an enumerated source), never a quoted reply, which keeps the rule that a message never carries log `extra` or third-party text. Throttled per account and kind like the other notifications. It extends the existing `spam_limited` kind rather than adding one.
2. **Safe mode is not a new state.** Every consumer already reads the verdict, so safe mode is those readers being consistent, and this section lists what each does today instead of adding a second mechanism:

| Consumer | Today for a `limited` account | Stage 3 |
|---|---|---|
| Warming | PeerFlood or a `limited` check → quarantine 48 h, a confirmed block → `spam_blocked` (DMs off; search, join, read, react stay on, i.e. the gentle mode) | unchanged: nothing is duplicated |
| Outreach, bulk messages, masstagging | skip a `limited` account | unchanged, now through `verdicts_for` (a dated limit that expired reads `unknown`) |
| Neurocomment | readiness and trust gates refuse `limited` ("spam limited") **unless the block is known and kept** (`spam_block_known`): a kept account is handed to neurocomment on purpose, because commenting needs no messages to strangers | **conflict with the request, see D14**: the request says neurocomment stops; the existing rule says a kept account may comment |
| Chat broadcast | **reads no spam status at all**; an account rests only after its own PeerFlood | new, small: a `limited` verdict rests the account through the broadcast's existing rest (`rest_for`, `spam_rest_seconds`), the same exit a PeerFlood takes, so a known block is not discovered by sending |

3. **"Подать апелляцию" button on the account card** calls the existing `open_account_web` (the account's own proxy and fingerprint; refused without a proxy as today) with a navigation target. `core/web_login/browser.py` navigates to `https://web.telegram.org/k/` today; for an open window it raises the window and then navigates it (`Page.navigate`), for a new one it launches on the target URL. Target candidates, in order: WebK's own deep link `https://web.telegram.org/k/#@SpamBot` (WebK resolves `#@username` itself), then the form Telegram's `tg://` links use on the web, `?tgaddr=tg%3A%2F%2Fresolve%3Fdomain%3DSpamBot`. **Neither is verified**: it is a live check on one account the owner chooses (D8), recorded in the PR that lands it. If the dialog does not open, the window still opens on WebK and the operator searches @SpamBot by hand; nothing breaks.
4. **Record of the appeal**: table `spam_appeals` (migration 116: account, `filed_at`, `filed_by` = the signed-in operator, the verdict and end date at that moment; one open row per account, closed by a `clean`). The operator presses "Апелляция подана"; the card shows when and by whom. Nothing infers it from the window. **Re-check**: by the end date (+ the jitter of D5) or, for an undated limit, `appeal_recheck_hours` (24) after filing, as an ordinary DECISION ask under the budget, so there are no extra probes; a button still follows the operator limits. **Lifting a block is unchanged: only an @SpamBot `clean` lifts it (D4)**, whether or not an appeal was filed.

**How the hard limit is pinned by tests (T18):** (a) an AST test: the appeal module imports nothing from `core.telegram_client` and no send, click or `Input.*` symbol; (b) a behaviour test against a fake CDP session records every command `open_account_web` sends for the appeal and asserts the allowlist `Page.navigate`, `Target.activateTarget`, `Page.bringToFront` and nothing else (no `Input.dispatch*`, no `Runtime.evaluate` that clicks or types); (c) a test that the gateway's only send to the bot is the probe's `/start`, with the history reader and the appeal sending nothing; (d) the appeal route takes no body that could become text.

## 6. Caller by caller: was → will be

"Name the difference, justify it, test it." `U` = a `deferred`/`refused` answer that the caller must not mistake for an unreadable reading.

| Caller (site) | Was | Stage 1 | Stage 2 | Stage 3 |
|---|---|---|---|---|
| Warming routine step (`_spam_step`) | asks when due; `unknown`/streak>0 asks every cycle; `unknown` → streak+1 | same rule through the gateway ROUTINE; **backoff replaces "every cycle"** (6→12→24→48 h); a `U` answer adds **nothing** to the streak (rich answer, not an `unknown` verdict); proxy-less / not-alive accounts are not asked | evidence can make it not due; adaptive interval; known-limited by PeerFlood is not re-asked until quarantine ends | probe inside the cycle after the presence flip |
| Quarantine recheck (`_quarantine.py:168`) | forced, 48 h | DECISION: probe allowed (48 h ≫ 6 h minimum); a `U` answer **keeps the window and retries at `retry_at`** without spending a recheck or the unreadable counter (new, small) | unchanged | human sequence |
| Hand-off (`_handoff_spam.py`) | cache first; refusals `spam_limited` / `spam_check_unavailable` | same; `U` → `spam_check_unavailable` with a retry time | decided by the fold: a high-confidence clean evidence ≤ max_age serves without asking | human sequence |
| Survival window / block recheck | cache-respecting; in-memory retry and spacing | MEASUREMENT through the gateway; the ledger replaces the in-memory spacing, so a restart no longer re-probes a failed account at once; a `U` answer does **not** stamp an attempt | unchanged: windows stay real @SpamBot answers (D11) | live-session preference |
| Neurocomment onboarding | cache-respecting + 30–120 s pause after a real probe | ROUTINE; the pause is kept but keyed on `probed` from the answer, not on a timestamp guess; the gateway spacing makes it a backstop | evidence | live session (the listener holds one) |
| Neurocomment ban confirmation (`bans.py:173`, `:327`) | forced, one per pair | DECISION; **N pairs of one account → one probe** (coalescing); a `U` answer reads as "not confirmed", i.e. no sticky ban (the safe side, already the contract) | a fresh `peer_flood`/limited evidence answers "account-wide" without asking | human sequence |
| Neurocomment unconfirmed-ban budget (`bans.py:272`) | cache-respecting | DECISION, same | evidence | same |
| Chat broadcast (`_seams.py:127`) | **always forced**; rest end: anything but `clean` halts the account for good | DECISION; forced becomes "unless a reading < 30 min exists"; **a `U` answer keeps the rest going to `retry_at` instead of the final halt** (new: today a failed probe halts the account permanently) | evidence | human sequence |
| Operator button | forced, unlimited | OPERATOR: ≥ 30 min hard minimum, `confirm_out_of_turn` under 24 h, 3 extra per day, served from the last reading inside 30 min; response gains `served_from`, `next_allowed_at`, `probed` | shows source and confidence | card and confirmation dialog |
| Masstagging `spamblock` write | direct `upsert_spam_status('limited', 'peer_flood')`, no journal, no lift | goes through the gateway as the same row (no behaviour change yet) | `peer_flood_send` evidence; no longer looks like a fresh @SpamBot reading | — |
| Outreach protection, masstagging fleet, trust, selection, board, table (readers) | read `limited` of any age; `unknown` = allowed | unchanged | read through `verdicts_for`: a dated limit that expired reads `unknown` (not skipped); an undated one stays `limited` until a clean arrives, and is rechecked (D5) | card shows the source |
| Appeal of a block | none: the operator opens the window by hand and finds the dialog | unchanged | the verdict-change notification (Stage 2) | notification with the account, the end date or "бессрочно" and the source; "Подать апелляцию" opens the window on the @SpamBot dialog; "Апелляция подана" is recorded and shown; re-check by the date or the budget; the program presses and sends nothing (5.12) |
| Chat broadcast for a `limited` account | sends until its own PeerFlood | unchanged | unchanged | rests through its existing rest (D14) |
| Frozen | account status only | unchanged | `freeze_seen` evidence; legacy projection `unknown` + detail `frozen`; probes refused (rule 1) | card |
| Lift of a spam block | any `clean` | any persisted **@SpamBot** `clean` | same: a passive clean never lifts (D4) | — |

*As built in Stage 1.* The seams keep their names and signatures (the tests patch them); the caller's name travels in a context variable (`spam_intel.caller_scope`), set at each call site, so no fake of the old `(account_id, *, force=False)` shape breaks. The domain seam binds the feature that IS the holder (`bound_asker("warming")`, `"chat_broadcast"`; neurocomment holds nothing). `SpamStatusVerdict` gains three never-stored optional fields (`served_from`, `retry_at`, `reason`) and the `POST /accounts/{id}/spam-check` response carries them. A postponed answer is `unknown` with `served_from="deferred"`; warming's cycle ignores it (no streak), the quarantine keeps its window (`quarantine_recheck_deferred`), the broadcast keeps resting, survival does not stamp an attempt (a `refused` one is stamped, so it is not retried hourly), onboarding paces on `served_from="probe"`. Masstagging's `limited` write goes through `spam_intel.record_peer_flood_limited` (the same row). The 30-120 s onboarding pause and warming's per-account cadence are kept; the gateway spacing is the backstop.

Behaviour differences that are intentional and each need a test: (1) forced no longer means "always"; (2) deferral is not unreadability, in warming, quarantine, broadcast and survival; (3) unrecognised reply → `unknown` (when `strict_parser`), with consequences in warming (streak), quarantine (extend, then `error` after 3 windows), hand-off (refusal), survival (journaled as before); (4) proxy-less and not-alive accounts are not asked; (5) the operator button is limited and warns; (6) the broadcast rest end no longer kills an account on a transient failure.

## 7. Staging

One PR per stage, the next starts only after the previous is merged on green CI and the owner's explicit yes. Each PR description carries the section 6 rows it touches and the list of checks actually run.

**Stage 1: gateway, budget, safe parser, history sweep.** Gateway + architecture test; ledger tables and migration 114; the budget (single-flight, coalescing, spacing, caps, backoff, not-alive/no-proxy refusals); all callers moved to the gateway through the existing seams with `caller` labels, business logic unchanged except the rows marked Stage 1 above; events; operator notification when the fleet budget is exceeded (`spam_budget` kind, `services/_notification_messages.py`); `sample_capture` on; **`strict_parser` shipped but off, with `parser_shadow` on** so the strict result and the legacy result are compared on real replies before the owner flips it (R3, D6); **the read-only history sweep** (below). No UI beyond the additive API fields.

*The history sweep (added after review, closes the urgent half of R3).* Production's `clean` verdicts for Spanish, Portuguese and French accounts (11 of 14) rest on the legacy fallback, and nothing waits for new probes to tell whether those accounts are really clean. The probe never deleted its dialog, so every account's past @SpamBot replies are still in it. `services/spam_intel/_history.py` reads each eligible account's dialog once (`messages.getHistory`, no `/start`, nothing marked read) and stores the replies as redacted samples (`source='history'`), with what the legacy and the strict classifier make of each; the stored verdict is not touched. Eligibility and pacing are the probe's: alive, with a proxy, not busy, one account behind a proxy at a time (`decide_history`: the permanent refusals and the spacing, none of the probe caps), a jittered gap between any two, one read per account (`history_read_at`). **It is a Telegram request on each eligible account, so it is off (`SPAM_INTEL__HISTORY_SWEEP=false`) and runs only after the owner's explicit go; the code and its tests ship now, the run does not.**

**Stage 2: evidence and passive signals.** Evidence table and migration 115, the fold, operator notifications when a verdict turns `limited` or `frozen` (the existing `spam_limited` notification already fires on `spam_status_refreshed`; Stage 2 extends it to passive evidence and adds `frozen`), the signal port and all passive sources, readers through `verdicts_for`, freeze as a verdict, `passive_decisions` **off** for the first two weeks (shadow comparison of the fold with the real @SpamBot answers).

**Stage 3: live probe, UI and the semi-automatic appeal (5.12).** `converse_with_spambot`, `has_live_client`, the multi-language lexicons built from captured samples, the card, the confirmation dialog, the warning. The first live run of the human sequence is on **one account the owner chooses**, with the owner's explicit approval (D8).

After each merge: 24-48 h later, read-only on the production machine: probes per account, errors, refusals, verdict changes, compared with section 3.2, and a report.

## 8. Test plan

Ids are referenced from the PR descriptions.

| Id | What | How |
|---|---|---|
| T1 | Architecture: no module outside `services/spam_intel` imports `core.telegram_client.check_spam_status` / `_spam`, calls `upsert_spam_status`, or `services.spam_status.refresh_spam_status`; `core` does not import `services.spam_intel` | AST scan in `tests/test_architecture.py` |
| T2 | Budget rules 1-12 one by one, with a controllable clock and rng, including the rule ordering | `tests/services/spam_intel/test_budget_*.py` |
| T3 | Restart: attempts, backoff and spacing survive a fresh process (re-open the DB, new single-flight map) | same, with a DB fixture |
| T4 | Fold: table-driven cases for every row of 5.3 and the supersede rule, plus `hypothesis` properties listed in 5.4 | `tests/services/spam_intel/test_fold.py`, `test_fold_properties.py` |
| T5 | Races: two simultaneous forced requests → one probe; five forced on one account in "check channels" → one probe; operator double-click → one | asyncio gather with a fake probe counting calls |
| T6 | Per caller, the five paths **fresh cache, needs probe, budget refused/deferred, probe failed, unknown**: warming routine, quarantine, hand-off, survival (window and block recheck), onboarding, ban confirmation (both budgets), broadcast (both sites), operator route, masstagging write | extend the existing test files named in 2.1 |
| T7 | `U` is not unreadable: warming streak unchanged on `deferred`; quarantine keeps the window; broadcast keeps resting; survival does not stamp | per caller |
| T8 | Parser: a fixture for each language and each variant (clean, limited with date, limited without date, reviewing, appeal, multi-message), built only from captured or owner-confirmed text; unrecognised → `unknown` and a stored redacted sample; ambiguous → `unknown`; legacy-vs-strict shadow comparison logged | `tests/services/spam_intel/test_parse*.py` |
| T9 | Redaction: names, usernames, phones, long digit runs are removed; the sample table stays bounded | same |
| T10 | Migrations 114 and 115 on a fresh and on a previous-version schema; idempotence; backfill; account delete removes the new rows | `tests/core/test_migrations_spam_intel.py`, `tests/core/repositories/` |
| T11 | Passive sinks: each `core` site emits the raw signal exactly once; a failing sink never changes the action result; PeerFlood reaches evidence from `execute`, `execute_read`, the chat gateway; `me.restricted` and freeze from the session check; no new RPC is added | `tests/core/test_spam_signals.py` |
| T12 | Lift: persisted @SpamBot `clean` lifts; passive clean does not; `lift_from_stored_answer` unchanged | `test_spam_block_lift.py` extended |
| T13 | Human probe (Stage 3): the call order against a fake client (resolve, history, typing, send, collect until quiet, read), no button click, no delete, handler registered before send, multi-message captured | `tests/core/test_spam_probe_human.py` |
| T14 | API and OpenAPI: additive fields, error contract, `confirm_out_of_turn`, generated types in sync | `tests/api/`, `tests/test_api_error_contract.py`, `tools.gen_api` |
| T15 | Frontend: confirmation dialog, source line, budget warning; Vitest per widget; Playwright where the editor spec exists | `frontend/` |
| T16 | Kill switches: each off-state reproduces the old behaviour on the original tests (the current test files stay green unmodified with the switches off) | parametrised over the existing suites |
| T18 | Appeal (Stage 3): the hard limit (AST allowlist, fake-CDP command allowlist, no send but `/start`, no free-text route); the notification carries the account, the date or "бессрочно" and the source and no third-party text; the record (who, when, one open row, closed by `clean`); the re-check timing under the budget; broadcast rests a `limited` account; migration 116 | `tests/services/spam_intel/test_appeal*.py`, `tests/core/test_migrations_spam_appeals.py`, `tests/core/web_login/` |
| T17 | Coverage stays ≥ 90 % branch, warnings fail, MEX check and re-pinned `bodyHash` for any grounded function touched | CI |

## 9. Risks and how each is closed

| Id | Risk | Closed by |
|---|---|---|
| R1 | A new table with a foreign key blocks account deletion (`account_spam_status` already needed a hand-written delete, `_accounts_delete.py:143-217`) | explicit delete lines + T10 |
| R2 | A passive "clean" wrongly suppresses a needed probe or lifts a block | weights 0 except the documented proof; `passive_decisions` off for two weeks; lift only on @SpamBot clean (D4); T4, T12 |
| R3 | **Strict parser parks healthy accounts**: 11 of 18 production accounts speak languages without markers (es 7, pt 3, fr 1); strict → `unknown` → warming streak, quarantine extension and `error` after 3 windows, hand-off refusals | `strict_parser` ships off; `parser_shadow` logs every difference; samples are captured first; lexicons for es/pt/fr are added from real samples; the owner flips the switch (D6). T8, T16 |
| R4 | Deferral misread as unreadable: warming streak, quarantine counter, **broadcast's final halt** | rich `VerdictAnswer`; per-caller changes in section 6; T7 |
| R5 | The survival counter changes meaning | `spambot_verdict` journaled only for real @SpamBot answers; windows stay real answers; no new lifecycle kind (a new kind needs a CHECK-table rebuild); T6 |
| R6 | 46 test files (93 references) patch or call `refresh_spam_status` / `check_spam_status` / `upsert_spam_status` | seam names and signatures kept; the shim stays; new behaviour via new optional keyword arguments; T16 |
| R7 | Generated API types and OpenAPI drift | `tools.gen_api` in the same PR; T14 |
| R8 | MEX grounding drift: `runtime-warming.md` and `runtime-telegram.md` pin function bodies by hash (`grounds_to`), and any pinned function we change turns `GROUNDING_DRIFT` red | re-read the note, fix its prose, re-pin `bodyHash` with `tools/mex_grounds_to.py` per `setup.md`; MEX check in CI |
| R9 | SQLite write contention from signal inserts on hot paths | coalescing per account/kind/10 min; one short statement in `to_thread`; errors swallowed; T11 |
| R10 | `services/session_guard/` collision | the design never touches it; `core/telegram_client/_session.py` is touched only to emit two signals; **stop and ask** if the other fix lands there first |
| R11 | Time handling (naive legacy stamps, dates without time zone in replies) | one `parse_moment`, naive = UTC (the survival and onboarding code already do this); dated limits expire at the parsed time +1 h; T4, T8 |
| R12 | The ledger starts empty and the first day after deploy allows a burst | migration backfill from the journal; quiet start; fleet spacing; T3, T10 |
| R13 | A live action on a production account | none planned in Phases 0-2; Stage 3 live validation only on one account chosen by the owner, with approval (D8) |
| R14 | Failure to emit a signal changes a Telegram action's result | the sink is awaited inside a guarded wrapper, errors swallowed and logged; T11 |
| R16 | The appeal drifts into automation (a button press, a typed reply, a captcha attempt) | not implementable by construction: no send path, no input dispatch in the appeal code; pinned by T18 (a)-(d). A request to automate any step is a design change, not a patch |
| R17 | `#@SpamBot` does not open the dialog in the installed WebK build | the window still opens; fallback deep link; live check on one account (D8); the operator finds the bot by hand |
| R18 | "Safe mode" duplicates or contradicts quarantine, `spam_blocked` or the kept-account rule | no new state (5.12 table); the one real conflict, neurocomment, is D14 |
| R15 | Stored reply samples hold personal data | redaction before storing, size and row caps, dedupe; T9 |

## 10. Decisions (all accepted as recommended on 2026-11-16; D4 "no", D8 only after Stage 3 and with the owner's explicit yes)

| Id | Question | Recommendation | Alternatives |
|---|---|---|---|
| D1 | Never probe an account without a proxy (the brief says "never") | **Yes**, with a clear message "assign a proxy to check". Note: 3 of 18 accounts have none, all 3 are `limited` and will not be re-checked until given a proxy. | allow with operator confirmation |
| D2 | Adaptive routine interval: 72 h → 120 h → 168 h after repeated clean readings, reset by any limiting evidence | **Yes** (fewer probes, same safety, since a limit also shows up as PeerFlood evidence) | fixed 72 ± 12 h |
| D3 | Operator button: hard minimum 30 min, confirmation under 24 h, 3 extra per account per day | **Yes** | stricter minimum (6 h) |
| D4 | May a passive `clean` lift a confirmed spam block? | **No**, only an @SpamBot `clean` | yes for `dm_stranger_ok`, which is documented proof, after the shadow period |
| D5 | Re-check accounts stuck `limited` outside warming (the 4 on production): at the date + a few hours, or every 5 days for undated | **Yes** (adds about 1 probe a day, fixes accounts blocked for 11 days) | leave to the operator |
| D6 | Strict parser rollout: ship off with shadow logging, then flip after es/pt/fr samples are captured and reviewed | **Yes** | flip at once and accept `unknown` for 11 accounts |
| D7 | Keep up to 200 redacted reply samples (≤ 1000 characters each) in the database | **Yes** | in-memory only (loses data on restart) |
| D8 | Live validation of the human sequence on one account of your choice, and what to learn: multi-message replies, whether buttons or a keyboard appear, the language, whether a limited account can write to groups it is in | **Yes, after Stage 3 is merged and only with your go** | skip; keep the sequence feature-flagged off |
| D9 | At a broadcast rest end, a failed or deferred check keeps the account resting instead of the final halt | **Yes** | keep the halt |
| D10 | Switches as environment settings applied on restart, not a runtime database setting | **Yes** (restart is routine here) | a settings table editable in the UI |
| D11 | Survival day-7/day-30 windows stay real @SpamBot answers | **Yes** (the answer is the product) | satisfy from passive evidence |
| D12 | Frozen shown as a separate verdict in the card; legacy projection `unknown` + detail `frozen` | **Yes** | add `frozen` to `SpamStatusKind` (touches every consumer and the API type) |
| D13 | Defer routine probes out of the account's local night | **Yes, in Stage 3** (warming already has the active-hours helper) | not at all |

| D14 | Neurocomment for a kept spam-blocked account: the request says stop it; today's rule lets a kept account comment (commenting needs no messages to strangers, and the hand-off is allowed with a known block) | **Keep today's rule** and stop only an account whose block is NOT known and kept; the operator decides \"keep\" on the card (the existing `Оставить`) | stop neurocomment for every `limited` account (changes a documented, tested contract) |
| D15 | The appeal re-check: at the end date + jitter, or 24 h after filing for an undated limit, as an ordinary decision ask | **Yes** | only by hand |
| D16 | The notification names the end date and the source | **Yes**; the date is derived by the program, never a quoted reply | no date |

## 11. Not verified (and therefore not assumed)

- @SpamBot's exact reply texts, buttons, multi-message behaviour, and whether the language follows the client `lang_code`. No official source; the lexicons wait for captured samples.
- Whether a limited account can post in groups it is in, join groups, or write to mutual contacts (the FAQ is silent). Hence weight 0 for those successes.
- Whether `USER_BANNED_IN_CHANNEL` is account-wide or per chat, and whether `PEER_FLOOD` on joins/reads is a spam verdict. Hence the weak weights and the unchanged participant-read confirmation.
- Whether a frozen account can talk to @SpamBot.
- Whether a pooled client that stays connected is seen as "online" by Telegram, and what the cold connect of an idle account looks like. Not changed here.
- The cause of each historical burst (nothing recorded the caller). Stage 1 records it from now on.
- Whether `#@SpamBot` (or the `tgaddr` form) opens the dialog in the WebK build the web window loads, and how @SpamBot's appeal flow looks (buttons, captcha): only a human in a window can tell; the program is built not to need either.
- Telegram's page for `channels.joinChannel` and `/faq_spam` (the bare URL) did not load for the fetcher; the `?setln=en` form did.
