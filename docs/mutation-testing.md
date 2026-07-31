# Mutation testing policy and audit

Mutation testing covers `services/` and `schemas/` with mutmut 3.6.0. The
checked-in baseline is a regression gate for the aggregate score, not a demand
that every individual mutant be killed. After successful collection Nightly
publishes the complete result list, actionable survivors, machine-readable
statistics, and a hotspot summary. If collection fails, the artifact instead
preserves every available partial snapshot and diagnostic log.

## Current audited baseline

| Result | Count |
|---|---:|
| Total | 10,818 |
| Killed | 9,243 |
| Survived | 1,573 |
| Timeout | 2 |
| Score | 85.4409% |

The original baseline was 6,524 killed, 2,303 survived, 6 timeout, and 8,833
total (73.8594%). The catalogue grew because current `main` and the new tests
cover additional production paths.

The current calibration uses CPython 3.13.14, mutmut 3.6.0, the deterministic
`mutation` Hypothesis profile, four mutmut workers, `PYTHONHASHSEED=0`, and
`TZ=UTC`. Its catalogue digest is
`8fc133e1ede73beea5f91ad870afc0f91be76e742d73e7642596d70305996be2`;
the digest binds mutant identities to the exact Python source paths and bytes,
so a semantic source change cannot silently reuse a reviewed timeout identity.
A complete clean local sweep measured the checked-in 9,243/1,573/2 floor.
GitHub Nightly is the clean-run confirmation.
Nightly preserves separate first-attempt and repair snapshots when repair is
needed because mutmut 3.6 resets non-selected statuses during a targeted run
and omits `not_checked` from `export-cicd-stats`.

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

Run the supported local sweep with the same preloading wrapper as Nightly:

```bash
HYPOTHESIS_PROFILE=mutation MUTMUT_MAX_CHILDREN=4 PYTHONHASHSEED=0 TZ=UTC \
  uv run python tools/mutmut_cli.py run --max-children 4
```

The wrapper preloads Loguru before mutmut 3.6 snapshots imported modules. This
keeps Loguru's queued record class stable across mutmut's coverage/stats module
reload. It also guards mutmut 3.6's generated-source lifecycle: before coverage
it restores any zero-length source copy and invalidates its stale metadata, and
after parallel mutant generation it regenerates any remaining zero-length copy
sequentially or fails explicitly. That guard intentionally wraps private mutmut
generation hooks, so the exact mutmut 3.6.0 version is pinned and validated by
the baseline. Neither workaround changes test selection or mutation scope.

## Remaining hotspot audit

The following groups explain every function currently reported in the top-20
Nightly hotspot table:

| Functions | Classification and rationale |
|---|---|
| `warming._state::_set_state` (71) | Mostly equivalent/low-value combinations across a deliberately generic partial-update builder. State-transition outcomes, compare-and-set races, invalid transitions, and persisted timestamps are behaviorally covered; testing every internal field-assembly permutation would couple tests to SQL construction. |
| `warming._runtime::reconcile_warming_runtime` (39), `start_warming` (17); `warming._runner::_warming_loop` (26); `warming._loop::run_loop_iteration` (33), `_finalize_after_cycle` (21) | Remaining orchestration branches are a mix of real lower-priority gaps and cosmetic observability mutations. Start/stop generations, persona carry-over, stale writes, restart reconciliation, cancellation, flood-wait, quarantine recovery, finalization, and idempotency are covered. Further work should target externally distinct failure outcomes, not task-map internals or exact log wording. |
| `warming._chat::_generate_chat_text`, `_reply_to_partner`; `warming._cycle::run_one_cycle` | Provider/Telegram outcome matrices and partial failures are covered. Survivors are concentrated in prompt wording, log metadata, optional fallback text, and rare action combinations. Exact prose assertions are intentionally avoided unless safety or routing semantics change. |
| `warming.board::_load_cards`, `_build_summary`; `warming.pacing::_morning_offset_seconds`; `warming.channels::add_channels`, `_normalize_channel` | Boundary, aggregation, timezone, normalization, deduplication, and idempotency contracts are covered. Remaining changes are primarily presentation text, equivalent normalization forms, or defensive invalid-input paths rejected by schemas. |
| `accounts._tdata::_run_tdata_import` (32); `accounts.profile_read::_fetch_live_or_error` (25); `accounts.profile::update_account_profile`; `accounts.login` and `accounts.media` | Success, validation, cleanup, cache/coalescing, force refresh, int64 serialization, partial Telegram failures, credential rollback, and media boundaries are covered. Remaining survivors are largely exception/log detail permutations and adapter payload variations without a distinct public outcome. |
| `neurocomment.engine::_handle_new_post`; `neurocomment._generate::_generate_acceptable`, `_register_challenge_failure`; `neurocomment.discovery::_run` (26), `adopt_candidates` (28); `neurocomment.challenge`, `_runtime`, and `board` | Post routing, acceptance boundaries, retry exhaustion, provider errors, deduplication TTL, active discovery progress/metadata, onboarding lifecycle, sweep behavior, and challenge escalation are covered. Remaining survivors mostly alter prompt/log text, board presentation, or equivalent retry bookkeeping; safety decisions and external actions remain the priority. |

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
| `services.warming._cycle.x__human_delay__mutmut_3` | Resolved to survivor | Equivalent boundary mutation; the timeout was caused by the test RNG fixture, detailed below. |

`services.warming._cycle.x__human_delay__mutmut_3` changes `hi <= lo` to
`hi < lo`. For an equal valid range, both versions return the same delay; the
mutant only consumes one extra random draw. The old fixture forced
`Random.random()` to return zero, which makes the standard library's
`lognormvariate()` rejection loop non-terminating. The fixture now provides a
finite pinned lognormal draw, so the mutant completes as an equivalent survivor
and the Nightly has no timeout. A test asserting the exact number of random
draws would still be implementation-coupled score padding.

The current catalogue produced three timeout candidates during calibration:

| Current mutant | Result | Rationale |
|---|---|---|
| `services.neurocomment.challenge.x__dispatch__mutmut_28` | Killed | Removing the dispatch deadline now fails the public solver operation within 200 ms instead of consuming mutmut's process timeout. |
| `services.accounts._tdata.x__run_tdata_import__mutmut_66` | Reviewed timeout | Collapsing distinct account locks to one key self-deadlocks a two-account batch. A 200 ms deadline killed it, but independent review rejected that wall-clock bound as flaky on loaded CI; the real batch/progress contracts use a safe 2 s bound. The exact identity is digest-bound. |
| `services.content.x_strip_markdown_delimiters__mutmut_11` | Reviewed timeout | Inverting the synchronous convergence loop creates an infinite CPU loop. Detecting it sooner requires a signal/subprocess watchdog whose timing and platform complexity would reduce test quality; mutmut's own process timeout is the correct isolation boundary. The exact identity is digest-bound and remains a baseline-reviewed timeout. |

## Confirmed production bugs found by the audit

1. A quarantine probe whose final compare-and-set lost a stop/restart race
   still published a successful recovery/extension/error outcome and audit
   event. Every final write now checks whether it applied before publishing the
   outcome, with real database race regressions for all three terminal paths.
2. Neurocomment deduplication captured the reservation timestamp before a slow
   provider call, shortening the intended 24-hour suppression window. The
   timestamp is now captured when the generated comment is accepted, with a
   time-based regression test.
3. Neurocomment shutdown used `asyncio.wait_for(gather(...))` as a bounded
   cancellation primitive. A child that repeatedly suppressed cancellation
   could make the supposedly bounded stop hang forever. Shutdown now waits only
   for the configured interval and safely consumes eventual task failures, with
   a regression that reproduces repeated cancellation suppression.
