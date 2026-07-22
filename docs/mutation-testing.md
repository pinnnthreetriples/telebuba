# Mutation testing policy and audit

Mutation testing covers `services/` and `schemas/` with mutmut 3.6.0. The
checked-in baseline is a regression gate for the aggregate score, not a demand
that every individual mutant be killed. Nightly always publishes the complete
result list, actionable survivors, machine-readable statistics, and a hotspot
summary.

## Current audited baseline

| Result | Count |
|---|---:|
| Total | 9,056 |
| Killed | 7,749 |
| Survived | 1,306 |
| Timeout | 1 |
| Score | 85.5676% |

The previous baseline was 6,524 killed, 2,303 survived, 6 timeout, and 8,833
total (73.8594%). The catalogue grew because additional covered production
paths became eligible for mutation.

This baseline was reproduced with CPython 3.13.14, mutmut 3.6.0, the
deterministic `mutation` Hypothesis profile, and four mutmut workers. Its
catalogue digest is
`dafc1da93243dc31095855748732ab3bd8c52dde160497e7a551a6d924f7bdf8`;
the digest binds mutant identities to the exact Python source paths and bytes,
so a semantic source change cannot silently reuse a reviewed timeout identity.
The complete first attempt had 25 infrastructure-level `not checked` entries;
a CLI-supported targeted repair resolved all of them (24 killed, 1 survived).
Both raw snapshots remain separate in the Nightly artifact, and the project
report overlays only those 25 identities. This is necessary because mutmut 3.6
resets non-selected statuses during a targeted run and does not expose
`not_checked` in `export-cicd-stats`.

## Classification policy

- **Testing gap:** a mutation changes a user-visible result, persisted state,
  external action, error, or idempotency/concurrency guarantee. Add a behavioral
  test.
- **Potential production bug:** the original behavior violates such a contract.
  Fix production code only with a separate regression test.
- **Equivalent mutation:** no observable production behavior changes for any
  valid input. Keep it and document material examples.
- **Low-value mutation:** only cosmetic log/prompt wording, redundant defensive
  syntax, type-only code, or an unreachable invalid-input path changes. Do not
  couple tests to implementation merely to kill it.
- **Timeout:** inspect the exact diff and selected tests. Replace reachable
  unbounded paths with immediate behavioral guards; retain only independently
  reviewed equivalent/low-value cases.

No source is excluded with `pragma: no mutate`.

## Remaining hotspot audit

The following groups explain every function currently reported in the top-20
Nightly hotspot table:

| Functions | Classification and rationale |
|---|---|
| `warming._state::_set_state` (71) | Mostly equivalent/low-value combinations across a deliberately generic partial-update builder. State-transition outcomes, compare-and-set races, invalid transitions, and persisted timestamps are behaviorally covered; testing every internal field-assembly permutation would couple tests to SQL construction. |
| `warming._runtime::reconcile_warming_runtime` (39), `start_warming` (19); `warming._runner::_warming_loop` (24); `warming._loop::run_loop_iteration` (33), `_finalize_after_cycle` (21) | Remaining orchestration branches are a mix of real lower-priority gaps and cosmetic observability mutations. Start/stop generations, stale writes, restart reconciliation, cancellation, flood-wait, quarantine recovery, finalization, and idempotency are covered. Further work should target externally distinct failure outcomes, not task-map internals or exact log wording. |
| `warming._chat::_generate_chat_text`, `_reply_to_partner`; `warming._cycle::run_one_cycle` | Provider/Telegram outcome matrices and partial failures are covered. Survivors are concentrated in prompt wording, log metadata, optional fallback text, and rare action combinations. Exact prose assertions are intentionally avoided unless safety or routing semantics change. |
| `warming.board::_load_cards`, `_build_summary`; `warming.pacing::_morning_offset_seconds`; `warming.channels::add_channels`, `_normalize_channel` | Boundary, aggregation, timezone, normalization, deduplication, and idempotency contracts are covered. Remaining changes are primarily presentation text, equivalent normalization forms, or defensive invalid-input paths rejected by schemas. |
| `accounts._tdata::_run_tdata_import` (32); `accounts.profile_read::_fetch_live_or_error` (21); `accounts.profile::update_account_profile` (19); `accounts.channel_posts::publish_account_channel_post` (15); `accounts.login` and `accounts.media` | Success, validation, cleanup, cache/coalescing, force refresh, int64 serialization, partial Telegram failures, and media boundaries are covered. Remaining survivors are largely exception/log detail permutations and adapter payload variations without a distinct public outcome; reachable distinct rollback/cleanup failures remain future testing gaps. |
| `neurocomment.engine::_handle_new_post` (15); `neurocomment._generate::_generate_acceptable`, `_register_challenge_failure` (15 each); `neurocomment.challenge`, `_runtime`, and `board` | Post routing, acceptance boundaries, retry exhaustion, provider errors, deduplication TTL, onboarding lifecycle, sweep behavior, and challenge escalation are covered. Remaining survivors mostly alter prompt/log text, board presentation, or equivalent retry bookkeeping; safety decisions and external actions remain the priority. |

`schemas/` has no major remaining hotspot. Schema work focuses on boundary
validation rather than asserting Pydantic internals.

## Timeout audit

All six timeout mutants from the original Nightly were inspected against their
exact diffs and selected tests:

| Original timeout mutant(s) | Result | Rationale |
|---|---|---|
| `services.neurocomment._runtime.x__onboard_active_campaigns__mutmut_25`, `__mutmut_27` | Killed | The onboarding lifecycle contract now proves one scan completes without a queued trigger and that rerun/cleanup decisions remain observable; inverted loop conditions fail immediately instead of hanging. |
| `services.warming._runner.x__is_live_generation__mutmut_2`, `__mutmut_8` | Killed | Generation identity, stale-task cancellation, and replacement-run contracts distinguish both altered live-generation decisions. |
| `services.warming._graduation.x__stop_warming_locked__mutmut_7` | Killed | Graduation tests require stop/cleanup to complete under the lock and cover repeated promotion plus cleanup failure behavior. |
| `services.warming._cycle.x__human_delay__mutmut_3` | Retained timeout | Equivalent boundary mutation, detailed below. |

`services.warming._cycle.x__human_delay__mutmut_3` changes `hi <= lo` to
`hi < lo`. For an equal valid range, both versions return the same delay; the
mutant only consumes one extra draw from the process-global random generator.
There is no seeded replay API or promised RNG-stream position, and the returned
value and distribution remain unchanged. An independent review therefore
classified this as an equivalent, low-value timeout. A test asserting the exact
number of random draws would be implementation-coupled score padding, so the
mutant is intentionally retained.

## Confirmed production bugs found by the audit

1. A stale warming generation could overwrite quarantine state after an
   in-flight quarantine probe. Compare-and-set guards now protect pre- and
   post-probe writes, with a real database race regression test.
2. Neurocomment deduplication captured the reservation timestamp before a slow
   provider call, shortening the intended 24-hour suppression window. The
   timestamp is now captured when the generated comment is accepted, with a
   time-based regression test.
