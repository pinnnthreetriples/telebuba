---
name: active-state
description: Live project state — what works, what is not yet built, known issues. Updated by the agent in the Record step of GROW after meaningful work.
last_updated: 2026-07-13
---

# Active State

This file is a **snapshot, not a changelog** — history lives in git / merged PRs / `.mex/events/`. Keep it short; compacted 2026-07-06.

## Current State

**Feature-complete and stable on `main`** (as of 2026-07-08, HEAD ≈ PR #204). The React SPA + FastAPI `/api/v1` split stack, the pixel-perfect design port, full backend wiring, warming personas, neurocomment Ф1+Ф2 with the vision captcha solver, the 2026-07-02 audit remediation (#197), nightly/CI hardening (#199), and the 2026-07-08 audit remediations — neurocomment solver/quota-race/locale-neutral onboarding (#201) and warming anti-ban fleet de-correlation (#202) — are all merged. CI (7 jobs) and the nightly (mutation ~72.7% kill / semgrep-full / hypothesis-extended) are green. Current work = the #203 warming fleet-correlation tuning (PR open, pending merge): per-account chronotype morning distribution (triangular fleet spread, stable base + daily jitter, clamped to the window), channel-affinity churn + off-affinity exploration + lower default ratio + per-deployment salt, cold-start spread across ~24h, and per-calendar-day weekend-biased quiet days — all in the new pure `services/warming/_fleet.py`. The 2026-07-10 targeted audit
refactor is also done (behaviour-preserving, all gates green): the two oversized
React components were decomposed into co-located `ui/` siblings —
`AccountEdit.tsx` (1274→~85 lines) into Session/Proxy/Device/Signals/Actions
sections (+`_shared.tsx`/`_styles.ts`), `NeurocommentPage.tsx` (1408→~570) into
Pipeline/ActivityLog/Idle/Listener/CaptchaSolver/Campaigns/HowItWorks cards
(+Odometer/SurfHover/CaptchaQueue); and the warming complexity cluster was eased
in-module (`start_warming` C17→A via `_carry_or_restamp`/`_cancel_existing_task`/
shared `_enforce_start_readiness`, `_run_chat_step` C12→B via `_should_chat`,
`_read_and_react`'s 5-tuple → `_ReadReactOutcome` dataclass). The third audit
item (telegram `_dispatch_action`/`_pick_reaction` tidy) was deliberately dropped
during execution — the extraction didn't clear the lint suppression and tripped
the file-size gate, i.e. net-negative churn on already-clear dispatch code.

A 2026-07-13 test-harness cleanup made the Gemini/OpenAI gateway tests hermetic:
shared fixtures now remove host HTTP proxy variables and close the reused clients
after every test. This fixes 28 environment-dependent failures under a SOCKS
`ALL_PROXY` without changing production gateway behaviour. Full backend and
frontend gates are green (1242 pytest / 281 Vitest).

A 2026-07-10 bug audit (parallel review of warming/neurocomment/core/api) then
fixed four confirmed correctness defects (PR open, no auto-merge): warming
`_gate_target_reached` no longer completes a quarantine/flood_wait account;
neurocomment reads its in-flight near-dup list live (post-reservation) instead of
a stale entry-time snapshot; `set_account_photo` returns 400 (not 500) on
`ValueError`; `decode_session_claims` returns None when no `AUTH__SECRET` is set. Two low-severity
hardening items were added to the same PR: the tdata-zip extractor now streams
members and counts the bytes actually written (instead of trusting the archive's
declared sizes) so a crafted archive can't overshoot the size cap and exhaust the
disk; and a broken loguru debug line in `phone_geo` (`%s` → `{}`) was fixed. A fifth
MED finding (daily action cap exceedable ~2x on mid-cycle restart) needs a design
call and is tracked in issue #208; the remaining audit findings were judged
not-worth-fixing (design opinions / accepted tradeoffs / cosmetic).

The 2026-07-11 profile-edit modal fix pass (#226, merged) repaired the five
operator-reported bugs (honest "обновлено" timestamp, refresh ✓/✗ feedback, real
«Сделать основным» via `photos.updateProfilePhoto`, int64 ids carried as strings
so photo/music delete stops silently no-op-ing, story view counts) and
parallelized the per-item thumb downloads. Its deferred follow-up is now done (PR
open): profile/story thumbnails are served from cacheable, cookie-authed image
endpoints (`GET /accounts/{id}/profile/{photos|stories}/{id}/thumb`,
`include_in_schema=False`, ETag + `Cache-Control: private, immutable` + 304)
instead of base64 `data:` URIs inlined in the snapshot JSON — the view now carries
`thumb_url` and the dead, never-rendered `avatar_data_uri`/`avatar_bytes` (plus its
redundant per-fetch avatar download) were dropped. Still deferred: right-sizing the
640px thumbs to the ~104px tiles + optimistic add/remove.

A follow-up story pass (PR open) adds two operator-requested story features: (1) a
per-story pin toggle in the stories tab — «📌 Навсегда» vs «🕑 24 часа» — backed by a
new `toggle_story_pinned` action (`stories.togglePinned`), service
`set_account_story_pinned`, and `POST /accounts/{id}/story/pin`; pinning keeps a story
on the profile past its 24 h active window, unpinning lets it expire. (2) Story reaction
counts alongside the existing view counts (`StoryViews.reactions_count` → snapshot
`reactions` → view `reactions`, rendered as a ❤ badge next to 👁).

A 2026-07-10 operator-reported UI bug pass (PR #210, no auto-merge) then fixed four
defects: (1) activity-log events now fully localized — `logEvent` ru/en dictionaries
are the single source of truth, `eventLabel` resolves `t('logEvent.<code>', {defaultValue: code})`
(the old ~116-entry allow-list was removed), the dynamically-named Telegram action
events (`telegram_{action}_{status}` f-strings) are labelled compositionally from a
`logEventTelegram.action`/`.status` map, and `tests/test_logevent_i18n_parity.py`
fails CI if any backend `log_event` code — or any action_type/status in that map —
lacks a translation (was: `tdata_*`, ~55 literal codes, and the whole dynamic
`telegram_*` family rendered as raw snake_case); (2) the warming card day-progress denominator is
now the account's real `target_days`, pluralized (`день`/`дня`/`дней` via i18next
`count`; the i18n string was hardcoded `/ 14 дней`); (3) the neurocomment "Решение
капчи" help tooltip uses the wrapping, left-aligned `tb-tip-pop--wide` variant (was
clipped by an unlayered `white-space:nowrap`); (4) warming↔neurocomment-listener
exclusivity is enforced both directions and at every choke point —
`ListenerBusyWarmingError` in `start_neurocomment` (→409) AND in
`reconcile_neurocomment_runtime` (startup/channel-edit skip+stop), the reciprocal
`AccountIsListenerError` in `start_warming` (→409), one shared
`core.db.list_warming_account_ids()`, plus a picker filter and the 409 surfaced in the
listener UI. All backend/frontend gates green (1079 pytest / 205 vitest).

A 2026-07-11 operator-reported UI fix (PR #212, cross-layer): the warming card
activity rail now reflects the engine's real cycle instead of the decorative
`cycles_completed % 6` formula (which could falsely show "Формирование отчёта"
as the live step on a running account with no timer). `activeStage` maps the
persisted `last_action` to the rail: waiting states (`sleeping`/`flood_wait`/
`quarantine`) park on "pause" (+countdown), a running/errored cycle shows its
real step, idle sits at start. The rail was **reordered to match the engine's
emission order** (`subscribe → read → reactions → stories → pause`) — stories
runs after reactions, and the rail renders by index, so a mismatched order would
un-fill a completed step. The fake `report` step (no backend action) was dropped.
Backend now emits a real `stories` progress step: `maybe_watch_stories` returns
whether a view landed, `run_one_cycle` advances the rail via `_watch_stories_step`
only when it did, and `stories` was added to `_loop._PROGRESS_STEPS` (between
`react` and `send_dm`). `set_online`/`join`→subscribe and the gated, rare
`send_dm` folds onto its neighbour (`stories`). +2 backend tests / +6 Vitest.

A 2026-07-13 operator-reported UI fix (PR open): a freshly-queued account showed a
blinking «Подписка на каналы» during the cold-start wait (up to
`cold_start_spread_hours` ~24h before the first cycle), implying work that wasn't
happening. The loop now persists the computed first-run time to `next_run_at`
(new `_persist_cold_start_schedule`, generation-CAS guarded → a restart resumes the
same wait), and the card detects the pre-start hold (`active` + `cycles_completed==0`
+ no `last_action`) to show «Выдержка перед стартом» + the real live countdown, with
the step rail idle until the first action lands. +2 backend / +1 Vitest.

A 2026-07-13 operator-reported follow-up (PR open): the warming card's activity
log now names **which channel** each join/read/react touched and **which emoji** a
reaction placed. The channel was already logged (`extra.channel`); the card just
didn't render it — now shows the channel label (else `@handle`, via a new
`channelLabels` prop from `WarmingPage`) and the reaction emoji. The emoji wasn't
recorded before: the gateway's `execute` now merges a `_DispatchResult.log_extra`
(`{reaction: <emoji>}` from `_dispatch_react_to_post`) into the `telegram_react_to_post`
log row. +1 backend test / +1 Vitest.

A 2026-07-13 follow-up made the warming activity log **honest about non-actions**:
reactions now log why nothing was placed — `warming_reaction_skipped` reason=`chance`
(persona dice missed; written by the *service*, since the gateway isn't called), or
a gateway `reaction_skip` of `no_posts`/`no_emoji` — and story views log `stories_seen`
(N vs 0 → "watched N" vs "no active stories"). Reuses the `_DispatchResult.log_extra`
seam; `dispatch_watch_peer_stories` now returns the seen count. Note: story viewing has
**no probability gate** (attempted once per cycle), so there's deliberately no "chance
missed" line for it. Cold-start spread was also lowered 24h→8h (#239) so the first cycle
lands the same evening/next morning. +3 backend tests / +1 Vitest / i18n ru+en.
Operator also doubled the persona session presets (`persona_sessions`): calm 2-4→4-8,
normal 5-8→10-16, active 10-14→20-28 (config + `.env.example` + the picker hints in
ru/en). Mature accounts run ~2× the daily cycles with ~half the inter-cycle pause; the
age/phase safety cap (`min(persona, phase)`) still throttles young accounts unchanged.

A 2026-07-11 feature added **phone-number authentication as a third add-account
method** (branch `phone-authentication`). The phone-code gateway/service/cache
(`_auth.py`, `services/accounts/login.py`, `_login_state.py`) and the
`request-code`/`submit-code` endpoints already existed for re-auth of an imported
account; the only gap was creating an account from a bare number. Added: `phone`
on `AccountCreate` (persisted in `_create_account`), `start_phone_login(phone,
label?)` (digits → `account_id`/`session_name`, duplicate → `SessionAlreadyExistsError`
→409), and `POST /accounts/start-login`. The Add-account wizard gained a third
"Номер телефона" method with a dynamic 2/3-step stepper: phone → account created →
proxy (existing step 2) → step 3 requests+confirms the login code (code+2FA), run
**after** proxy assignment so the first Telegram connect uses the account's proxy
(operator-confirmed ordering). New log-event `phone_login_started` (ru/en labels).
All gates green (1086 pytest / 207 vitest).

A 2026-07-11 profile-editing audit (two sonnet subagents, backend+frontend,
cross-verified adversarially) found and fixed in one PR: (1) **clearing
last_name/username/bio was broken end-to-end** — the SPA sent `null` for a
blanked field, but `None` means "leave unchanged" everywhere downstream
(Telethon omits the TL flag; the repo write skips `is not None`), so clearing
bio/username silently no-opped and clearing last_name desynced DB from
Telegram. Contract fixed as **`"" clears, None leaves unchanged`** across all
layers (SPA now sends trimmed plain strings; `_dispatch_update_profile` dropped
the `last_name or ""` coercion); (2) `flood_wait_seconds` was dropped by the
seven `raise ValueError(status)` blocks — new `services/accounts/_result.py`
(`AccountActionError` + `raise_for_result`) carries the code +
`retry_after_seconds` into the envelope's `fields`; (3) the story-image
normalisation error was hardcoded Russian prose → `StoryImageNormalisationError`
code `story_image_invalid` (mirrors the video path; FE maps both codes now —
`story_video_invalid` previously leaked raw); (4) server-side Telegram limits
(64/64/70, username `^(?:[A-Za-z][A-Za-z0-9_]{4,31})?$` — the optional group
admits `""`=clear) on `AccountProfileUpdateRequest`+`UpdateProfile`, mirrored in
the zod schema with i18n errors; (5) UX: ProfileModal auto-seeds pristine form
fields from the live snapshot (stale-prop overwrite fixed), dirty-close asks
before discarding, ConfirmModal got async opt-in (closes only on success),
photo/music uploads disable+spin while pending, `invalidateQueries()` blanket
calls scoped, `profiling` derived from the live list, Modal got focus
trap/restore. Deliberately skipped: B8 ownership cross-check on photo/music
removal (single-operator, Telegram self-scopes). Caveat: 4-char Fragment
collectible usernames are blocked by the 5-char minimum (matches
`account.updateUsername` RPC rules). Gates: 1121 pytest / 95% branch, 227
vitest / 96% lines.

A 2026-07-11 profile-editing bug pass (operator-reported, this branch) fixed
five defects in the edit-profile modal: (1) **photo/music delete silently
failed** — Telegram `photo_id`/`access_hash`/`file_id` are int64 (~19 digits)
but crossed the JSON edge as *numbers*, so the SPA rounded them past 2^53 and
sent back an `InputPhoto` Telegram didn't recognise (dropped silently, 200
returned, modal closed "as if" it worked; story delete survived only because
`story_id` is small). Fixed by carrying those ids as **strings** across the
JSON boundary (`_Int64Str` in `schemas/profile_media.py`, `str(...)` in
`account_profile_view`, `_decode_id` parse in the API); (2) **«Сделать
основным» was a dead label** (the old `ponytail:` comment wrongly claimed no
RPC) — implemented for real via `photos.updateProfilePhoto`: new
`SetMainProfilePhoto` action → `_set_main_profile_photo` gateway helper →
`set_account_main_profile_photo` service → `POST /accounts/{id}/photo/main` →
`setAccountPhotoMainMutation` → button; (3) **«Обновлено только что» was frozen
fake info** — the relative-time label used a render-time `Date.now()` and never
re-computed; added a 30 s tick; (4) **«Обновить» gave no outcome** — replaced the
`refreshing` bool with an `idle/loading/ok/error` machine (green ✓ / red ✗,
1.4 s auto-reset; a 200-with-`error` counts as failure); (5) **stories showed no
view count** — captured `StoryItem.views.views_count` into
`TelegramStoryThumb.views` → `ProfileStoryView.views` → «👁 N» badge. Also a
cheap load win: profile-photo and story thumbnail downloads now run via
`asyncio.gather` instead of serial awaits. Deferred (documented in the PR):
serving thumbnails from a cacheable image endpoint instead of inlining base64 in
the snapshot JSON. Gates: 1165 pytest, 241 vitest, aislop clean (the profile-media
`case` block in `_dispatch_action` was collapsed to the wildcard arm — the media
dispatcher's own `case _` already guards unknowns — keeping the file under the
size budget).

A 2026-07-11 neurocomment audit (operator-reported: NOXX campaign never
comments, board stuck on «Комментарии выкл.», reconcile "loop") diagnosed via
live-DB forensics + three sonnet subagents and fixed in one PR: (1) **root
cause — onboarding only ever ran from `start_neurocomment`**, so a campaign
created/edited *after* Start (NOXX: listener started 23:20, campaign built
00:55) never got readiness rows → every post logged
`neurocomment_no_account_available`. Now `reconcile_if_running` and
`reconcile_neurocomment_on_startup` also call `_ensure_onboarding_running`,
`assign_account_to_campaign` reconciles a running listener, and a trigger
arriving mid-run queues one coalesced rerun (`_ONBOARD_RERUN`); (2) the
"reconcile every second" was **not** an infinite loop — each of 10 sequential
`link_channel` POSTs re-ran reconcile which re-joined ALL channels (~55
JoinChannel RPCs in 16 s, real flood risk); reconcile's join sweep is now gated
by a process-lifetime `_JOINED_CHANNELS` set (failed joins retry next call);
(3) the board's frontend fallback for an account with zero readiness rows
collided with the real backend `comments_off` status — now a frontend-only
`no_data` status («Нет данных»), and `deriveRows` prefers the account's
`pinned_channel` row over first-joined. Gates: full pytest suite + vitest green.

A 2026-07-11 second neurocomment audit (four parallel sonnet auditors over
engine/challenge/onboarding/board/api, then three implementers on disjoint
files) fixed eight defects in one PR — the recent trigger fix (1531b76) was
re-verified regression-free. HIGH: `_resolve_group_for_join` used only
`accounts[0]` for the linked-group read, so one dead/banned first session
marked every account `resolve_failed` and blocked the channel for all — now
tries each session in order, first success wins. HIGH: `resolve_pending_outcome`
was a read-then-write race (two concurrent `_classify_post`s for the same
account+channel could both consume one pending challenge row and double-count
the channel failure counter) — the UPDATE now guards on `outcome='pending'` and
returns on rowcount (winner-takes-all). MED: deactivating a channel left
accounts pinned to it stranded (pin never cleared → silently excluded from
selection+onboarding forever) — `_deactivate_channel` now nulls the pin in the
same txn. MED: the deletion sweep aborted the whole pass on one channel's
bookkeeping fault — `_sweep_once` now isolates per channel
(`neurocomment_sweep_channel_failed`). MED: `NeurocommentSettingsUpdate` lacked
the `reply_delay_min<=max` validator its config twin enforces — added. MED: the
board's backend channel badge showed `throttled` for a channel with zero
readiness rows — new `no_data` `ChannelStatus` (frontend already handled it;
API client regenerated). LOW: unbounded campaign `name`/`prompt` at the trust
boundary — `max_length` added; LOW: `future.set_result` race in the challenge
wait handler — `future.done()` re-check. Deferred (reported, not fixed):
shutdown-cancel can mis-mark a just-delivered comment `failed` (microscopic
window, existing guard mostly covers); no periodic re-onboard for `joining`
pairs (event triggers + boot cover it); solved-challenge cache has no
TTL/invalidation; RPC-hang stale-claim reclaim and HTTPException-locale are
systemic/codebase-wide. Gates: 1140 pytest / i18n-parity green, ruff+ty clean,
frontend tsc+vitest green.

A 2026-07-11 board-honesty UX follow-up (PR open, auto-merge): a slow jittered
onboarding read as a stalled «Нет данных» with no live feedback. Root cause —
clean joins write readiness rows but emit no log event, so the SSE stream is
quiet and the board only moved on the 30s poll. Fix: the runtime status now
carries a real `onboarding: bool` from the live `_ONBOARD_TASK` handle
(`is_onboarding_running`; not a readiness-count heuristic — a `comments_off`
channel yields no row and would stall a counter). Frontend: while `onboarding`,
the board polls 4s (not 30s), the header shows a pulsing «Онбординг идёт»
indicator, and a not-yet-armed account's status cell animates «Онбординг N/M»
(ready/target; target = 1 if pinned else channel count) instead of `no_data`.
Once onboarding finishes the flag flips and the real per-channel statuses render.
Gates: neuro runtime+api 76 pytest green, ruff+ty clean; frontend tsc+eslint+
vitest green, API client drift-free (only the `onboarding` field).

A 2026-07-11 log-informativeness pass (PR open): the neurocomment activity log
now reads by meaning, not just level. Frontend `logSeverity` (shared/lib) recolours
by event code — attempted-but-failed (`*_failed`/`_exhausted`/`_dropped`) is red
even when logged INFO, deliberate skips/pauses (`_skipped`/`no_account`/`_cooldown`)
amber, successes green; the "Errors" stat reuses it. `ActivityLogCard` lines gained
the channel + a translated reason (`logEventReason.*`) and a hover hint
(`logEventHint.*`, "why + fix"). Backend now records *why*: `_select_account`
returns `_Selection(account_id, reason)` (quota/cooldown/not_ready/unhealthy/
no_accounts_linked via the single `_account_block_reason` gate ladder that
`_is_eligible` now delegates to), and `_generate_acceptable` returns
`_GenOutcome(text, reason)` (gemini_error/gemini_rate_limited/gemini_empty/too_long/
not_acceptable/duplicate) — both surfaced in the `no_account_available` /
`generation_exhausted` log `extra`. Settings label clarified «Комментариев в час
(на аккаунт)». Diagnosis behind it: one account served 9+ channels → 113/258 recent
events were `no_account_available` (capacity, not a bug).

Same PR, second pass: (1) a **clear-logs** action — `DELETE /api/v1/logs?event_prefix=`
(`clearLogs` op → `LogPurgeResult`, repo `purge_logs(prefix)`, service `clear_logs`);
the neuro log card gained a trash button (shown only with rows) → confirm → clears
`event_prefix=neurocomment` only. (2) Bug fix: the campaign-prompt modal showed an
account's arbitrary first-readiness channel; it now shows `pinned_channel` or the
campaign name (unpinned = whole-campaign scope). Gates: full 1171 pytest green
(strict), ruff+ty+aislop clean; frontend 244+ vitest + tsc+eslint+steiger green;
API client regenerated (adds `clearLogs`, drift-free).

Same PR, third pass — **deleted-comment tracking** (near-real-time). The deletion
sweep already computes the vanished comment ids; it now also stamps them: migration
#27 adds `neurocomment_comments.deleted_at`, `mark_comments_deleted` (new
`_deletions.py` — `_comments.py` was already 438 lines, grandfathered over the 400
cap, so the new code lives outside it) marks posted-and-still-live rows idempotently,
and the sweep logs `neurocomment_comment_deleted` per fresh batch. Sweep interval
dropped 1800→300s (`.env.example` mirrored; arch test enforces value match).
Surfacing: feed + history show deleted comments struck-through with a danger badge;
the board channel cell gets a «N удалено» chip (`NeurocommentChannelRow.deleted_recent`,
counted in `board.py` via `_ChannelFlags`); `logSeverity` colours `*_deleted` red.
True-instant `MessageDeleted` gateway handler is a deliberate follow-up (needs live
group→channel peer-mapping validation — can't smoke-test blind against the untouchable
live instance). Gates: full 1173 pytest green (strict), ruff+ty+aislop clean; frontend
249 vitest + tsc+eslint+steiger green; client regenerated (`deleted_at`, `deleted_recent`).

A 2026-07-12 CI fix (this branch): the **nightly mutation job had been red since
2026-07-10** — it never tested a single mutant, dying at the baseline stats run
because `tests/test_logevent_i18n_parity.py` reads `frontend/src/shared/i18n/*.json`
but `[tool.mutmut].also_copy` didn't copy that path into the `mutants/` sandbox
(FileNotFoundError → "failed to collect stats"). Added `frontend/src/shared/i18n`
to `also_copy` and documented the contract (any test reading a repo file outside
`services/`/`schemas/` must be added there). Verified by reading mutmut's
`copy_also_copy_files` (dir → `copytree` into `mutants/`) + auditing all
filesystem-reading tests; mutmut can't run natively on Windows (needs WSL), so the
live proof is the ubuntu nightly.

A 2026-07-13 operator-reported neurocomment pass (this branch) shipped three
fixes: (1) **channel ban-check false-negatives** — the "Проверить каналы" probe
resolved a channel's linked discussion group only from `ChatFull.chats`, which
Telegram omits for some channels, so the probe degraded to `unknown` (chip stayed
uncoloured) while the account could actually comment. Now it falls back to
`get_input_entity(linked_id)` off the warm session cache (same idiom as
`_read_challenge`); the identical latent bug in `_dispatch_check_messages_alive`
(deletion-sweep liveness) was fixed via a shared `_resolve_linked_group_entity`
helper. (2) **precise quota reasons** — `no_account_available` now logs
`quota_hour` (per-account/hour) vs `quota_day` (per-channel/day) instead of a
generic `quota`, so the log names which cap was hit (`_quota_block_reason` replaces
`_quota_ok`/`_under_quota`; `_BLOCK_PRIORITY` split). (3) **auto-ban skip (#30)** —
a post that fails with `UserBannedInChannelError` now marks the (account, channel)
pair with a new sticky `neurocomment_readiness.banned` flag (migration #30):
selection excludes it, a re-onboard can't revive it (onboarding leaves it alone
like an operator skip), and it logs `neurocomment_account_banned` (red in the
feed). Recovery: a live `can_send` verdict from "Проверить каналы" lifts the ban
(`clear_pair_banned` → banned=0, ready=1); `retry_pair`/`delete_readiness` also
clear it. Board surfaces a red «Забанен» channel status (aggregate: ready wins over
a banned sibling account). Ban is classified separately from the solver-clearable
gates (no challenge back-off / pending-resolve). Deferred: per-account (not
channel-aggregate) ban badge in the work-row would need a `deriveRows` rework.
Gates: 1232 pytest (strict, ≥90% branch), 271 vitest + tsc green, ruff+ty clean,
API client regenerated (`banned` state + `quota_*` reasons drift-free).

## Not Yet Built (deliberate)

- **#149 HITL captcha canary** — operator-run; never an agent task.
- PM-delivered captchas (Rose non-button modes) not watched; grid/slider/jigsaw give up by design.
- Proxy unassign-from-account: backend exists (`POST /proxies/unassign`), no UI trigger.
- `ListenerEditModal` persists the picked listener only while the runtime runs.
- Accounts table status filter / column sort / bulk actions — backend supports, UI gap.
- Photo «сделать главной» — implemented as promote-then-delete-old (raw
  `updateProfilePhoto` mints a new photo + leaves the original, so we delete the
  stale id after, mirroring TDLib `inputChatPhotoPrevious`).
- Warming per-action numeric limits stay auto/config (read-only in UI, auto-cap ADR).
- **Interest-partitioned channel catalog** — the durable fix for cross-account channel overlap (joins are permanent + the pool is shared, so churn/exploration only bound convergence, not eliminate it). Explicitly deferred by #203 as a separate follow-up.

## Known Issues

- aislop gate = `uv run python tools/aislop_gate.py`; `python -m aislop` does not work (npm tool).
- Local `prettier --check .` floods with CRLF false positives (LF in git, CI green) — check only changed files after `prettier --write`.

## Open Decisions

- **Account lifecycle enum beyond session health** — `accounts.status` (session health) vs `warming_account_state.state` (runtime lifecycle); a unified business lifecycle is undecided. (`context/telegram.md`)
- **Project purpose / "why"** — deliberately deferred; not documented anywhere.
