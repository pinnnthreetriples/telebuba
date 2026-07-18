# Mutation testing policy and audit

Mutation testing covers `services/` and `schemas/` with mutmut 3.6.0. The
checked-in baseline is a regression gate for the aggregate score, not a demand
that every individual mutant be killed. Nightly always publishes the complete
result list, actionable survivors, machine-readable statistics, and a hotspot
summary.

## Current audited baseline

| Result | Count |
|---|---:|
| Total | 9,052 |
| Killed | 7,774 |
| Survived | 1,277 |
| Timeout | 1 |
| Score | 85.8816% |

The previous baseline was 6,524 killed, 2,303 survived, 6 timeout, and 8,833
total (73.8594%). The catalogue grew because additional covered production
paths became eligible for mutation.

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
| `warming._state::_set_state` | Mostly equivalent/low-value combinations across a deliberately generic partial-update builder. State-transition outcomes, compare-and-set races, invalid transitions, and persisted timestamps are behaviorally covered; testing every internal field-assembly permutation would couple tests to SQL construction. |
| `warming._runtime::reconcile_warming_runtime`, `start_warming`; `warming._runner::_warming_loop`; `warming._loop::run_loop_iteration`, `_finalize_after_cycle` | Remaining orchestration branches are a mix of real lower-priority gaps and cosmetic observability mutations. Start/stop generations, stale writes, restart reconciliation, cancellation, flood-wait, quarantine recovery, finalization, and idempotency are covered. Further work should target externally distinct failure outcomes, not task-map internals or exact log wording. |
| `warming._chat::_generate_chat_text`, `_reply_to_partner`; `warming._cycle::run_one_cycle` | Provider/Telegram outcome matrices and partial failures are covered. Survivors are concentrated in prompt wording, log metadata, optional fallback text, and rare action combinations. Exact prose assertions are intentionally avoided unless safety or routing semantics change. |
| `warming.board::_load_cards`, `_build_summary`; `warming.pacing::_morning_offset_seconds`; `warming.channels::add_channels`, `_normalize_channel` | Boundary, aggregation, timezone, normalization, deduplication, and idempotency contracts are covered. Remaining changes are primarily presentation text, equivalent normalization forms, or defensive invalid-input paths rejected by schemas. |
| `accounts._tdata::_run_tdata_import`; `accounts.profile_read::_fetch_live_or_error`; `accounts.profile::update_account_profile`; `accounts.channel_posts::publish_account_channel_post` | Success, validation, cleanup, cache/coalescing, force refresh, int64 serialization, partial Telegram failures, and media boundaries are covered. Remaining survivors are largely exception/log detail permutations and adapter payload variations without a distinct public outcome; reachable distinct rollback/cleanup failures remain future testing gaps. |
| `neurocomment._generate::_generate_acceptable`, `_register_challenge_failure` | Acceptance boundaries, retry exhaustion, provider errors, deduplication TTL, and challenge escalation are covered. Remaining survivors mostly alter prompt/log text or equivalent retry bookkeeping; safety decisions and external actions remain the priority. |

`schemas/` has no major remaining hotspot. Schema work focuses on boundary
validation rather than asserting Pydantic internals.

## Timeout audit

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
   in-flight cycle. Compare-and-set guards now protect pre- and post-probe
   writes, with a real database race regression test.
2. Neurocomment deduplication recorded the pre-provider timestamp after a slow
   generation call, shortening the intended 24-hour suppression window. The
   timestamp is now captured when the generated comment is accepted, with a
   time-based regression test.
