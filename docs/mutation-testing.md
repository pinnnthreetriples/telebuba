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
| Total | 15,471 |
| Killed | 12,735 |
| Survived | 2,506 |
| Timeout | 230 |
| Score | 83.5575% |

The original baseline was 6,524 killed, 2,303 survived, 6 timeout, and 8,833
total (73.8594%). The catalogue grew because current `main` and the new tests
cover additional production paths.

The current calibration uses CPython 3.13.14, mutmut 3.6.0, the deterministic
`mutation` Hypothesis profile, four mutmut workers, `PYTHONHASHSEED=0`, and
`TZ=UTC`. Its catalogue digest is
`da10806f3d12c1096547daaf9351f6dca43d077bf1cdd2a6a43b8ad165f45f73`;
the digest binds mutant identities to the exact Python source paths and bytes,
so a semantic source change cannot silently reuse a reviewed timeout identity.
The floor is measured on the GitHub runner, not locally. A clean local sweep
scores higher, but the hosted runner is slower and more contended, and pinning
the floor to the best machine made every Nightly red.

The score is `killed / (total - timeout)`: the share of mutants mutmut reached a
verdict on. A timeout is not a survivor, it is "no answer inside the allowance",
and that allowance is `timeout_multiplier` times a duration MEASURED per run.
Counting timeouts as unkilled therefore made the score track runner load rather
than test quality.

Four sweeps of one unchanged catalogue show it. Timeouts ranged 124-224 and
never converged — their union grew 195, 200, 205, 230 — while survivors settled
at 2,506 after the second sweep and moved by thirteen identities in total. Over
the whole population those sweeps span 0.67pp of score; over the decided
population they span 0.13pp.

So the gate rests on what is stable. The catalogue digest, the reviewed survivor
identities and the score over decided mutants all fail the build. A new timeout
identity is named in the report for review and does not. Survivors keep their
serial recheck: one seen under four-worker load is rerun alone before it counts,
which is how `start_neurocomment__mutmut_1` was caught as noise and kept out of
the floor.

The 12,735/2,506/230 floor is the union of all four sweeps, and every one of them
clears it: 83.6148%, 83.6509%, 83.7102%, 83.5837%.

The first post-merge sweep also found one mutant mutmut could not run at all:
`services.warming._seams.x_refresh_spam_status__mutmut_1`, reported as `no
tests`. The warming seam fences the quarantine spam probe with the runtime lease
exactly as it fences gateway dispatch, but every test patches that seam, so its
body had never executed. The gateway half had a lease test and this half did
not; it has one now. `no tests` is reported and gated by name rather than
refusing to build the report — one such mutant used to leave Nightly with no
`report.json`, no summary, and a generic "required artifact missing" error. The
seam now has fourteen live mutants where it had one unrunnable one, twelve of
them killed.

The twelve identities below were measured before that merge, so their numbers
belong to the previous catalogue. They are
counted as survivors, so the gate under-reports rather than flapping, and ten
have since been made deterministic:

- Seven in `services.accounts.profile_read` (`x__fetch_and_maybe_cache__mutmut_6`,
  `__mutmut_8`, `__mutmut_9`; `x_fetch_live_account_profile__mutmut_14`,
  `__mutmut_17`, `__mutmut_19`, `__mutmut_20`) all mutate the default of
  `_CACHE_GEN.get(account_id, 0)` / `.pop(account_id, None)`, which only matters
  when the key is absent. The test fixtures called
  `invalidate_account_profile_cache` as isolation, but that is production
  invalidation: it bumps the generation and leaves the key behind. Whether a
  mutant met a populated dict depended on what ran earlier in the same worker
  process. `tests/services/conftest.py` now clears the module state outright.
- `services.warming.pacing.x__next_utc_midnight__mutmut_7` drops `second=0`.
  Every test reaching it pinned `now` to a whole minute, so the mutation was
  equivalent for them; the live loop calls it with `datetime.now(UTC)`, where it
  persists a `next_run_at` seconds off. A direct contract test now passes a
  non-zero second.
- `..._watch.x_reconcile_neurocomment_runtime__mutmut_54` drops `account_id` from
  the `neurocomment_channels_unwatched` warning, and
  `..._watch.x__resubscribe_unwatched__mutmut_33` misspells its `error_type`
  field. Both are the only signal naming the subject or the cause, so the
  existing runtime tests now assert them.

Clearing the module state cost four kills it had been buying by accident: the
old fixtures called `invalidate_account_profile_cache` on every test, which
exercised the generation bump and left the key that made the stale-eviction
reset observable. Those contracts are now tested on purpose — a stale eviction
establishes the generation it resets, and two back-to-back clear-all
invalidations prove the generation advances instead of being stamped. Stubbing
`log_event` in the resubscribe test cost three more, because the real callee had
been validating the level; the test asserts the level itself now.

Two are left deliberately. `..._watch.x_reconcile_neurocomment_runtime__mutmut_13`
only changes the case of a log event name, which the static i18n parity test
already guards on the real tree; a duplicate runtime assertion would be score
padding. `services.neurocomment._join.x_run_join_pass__mutmut_44` could not be
identified with confidence, because coverage filtering shifts that function's
mutant numbering between a local generation and the measured catalogue.
Nightly preserves separate first-attempt and repair snapshots when repair is
needed because mutmut 3.6 resets non-selected statuses during a targeted run
and omits `not_checked` from `export-cicd-stats`.
It also rechecks with one worker every first-attempt timeout or survivor that
the baseline has not reviewed by name. Four-worker contention distorts both
verdicts: it stalls a mutant into a timeout, and it lets a slow test pass with a
mutant the same test catches when run alone. Only the completed serial status is
overlaid; a reviewed identity or a non-selected row keeps the official
first-attempt status. Because `reviewed_survivors` names every mutant the
baseline measured as surviving, a score regression is attributable — the gate
reports the exact identities that changed, not just a lower percentage.

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

The current-main catalogue produced eleven timeout candidates in the official
four-worker calibration. Seven were already serially proven timeouts. The four
new `_reply_and_post` candidates were all killed by the automatic one-worker
repair. Parallel-only timeout noise stays in the raw first-attempt artifact.

On the hosted runner six of those seven reproduce; `_strip_fence_tags__mutmut_8`
is killed there in both calibration sweeps, so the runner baseline reviews six
timeout identities. The row below records why it is a timeout when it is one.

| Current mutant(s) | Result | Rationale |
|---|---|---|
| `services.content.x_strip_markdown_delimiters__mutmut_11` | Reviewed timeout | Inverting the synchronous convergence loop creates an infinite CPU loop. Detecting it sooner requires a signal/subprocess watchdog whose timing and platform complexity would reduce test quality; mutmut's own process timeout is the correct isolation boundary. The exact identity is digest-bound and remains a baseline-reviewed timeout. |
| `services.neurocomment._llm.x__strip_fence_tags__mutmut_8` | Killed on the runner | Inverting the synchronous convergence condition loops forever for already-clean text; a process boundary is the reliable watchdog. Local sweeps time out on it, the hosted runner kills it, so it is not a reviewed timeout in the checked-in baseline. |
| `services.neurocomment._generate.x__sleep_beating__mutmut_2`, `__mutmut_16`, `__mutmut_17` | Reviewed timeout | The mutations prevent the heartbeat countdown from reaching a negative/zero terminal state and reproduce serially as unbounded loops. |
| `services.warming._chat.x__maybe_inter_account_chat__mutmut_10` | Reviewed timeout | Marking `None` instead of the selected inbox row prevents the oldest-unreplied query from advancing and reproduces serially. |
| `services.warming._graduation.x__stop_warming_locked__mutmut_9` | Reviewed timeout | Removing the configured cancellation deadline makes a cancellation-suppressing task wait without a bound and reproduces serially. |
| `services.neurocomment._reply_wait.x__reply_and_post__mutmut_45` through `__mutmut_48` | Repaired parallel-only timeout | The official four-worker sweep timed out; the automatic serial repair killed all four, and the effective report overlays those completed results. |

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
