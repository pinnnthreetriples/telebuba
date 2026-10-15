---
name: change-survival-counter
description: Change the survival counter of warmed accounts, its outcome rules or the day-7/day-30 @SpamBot probes.
last_updated: 2026-10-15
edges:
  - target: context/runtime-warming.md
    condition: warming stints, graduation, quarantine or the @SpamBot schedule
  - target: context/architecture.md
    condition: layer boundaries, gateways or system design
---

# Change Survival Counter

- Outcomes come from the lifecycle journal; `load_survival` also reads the live accounts (labels, and each row's creation as a second life boundary — the deletion row is best-effort) and warming rows (cohort fallback). The unit is the account LIFE, counted once: lives split at each `deleted` row and at the live row's birth. Its waves are its stints: a wave opens on a new stint start, numbered by the start row's `wave`, else in order; history rebuilt from the logs is one wave.
- Release is the life's first graduation, in whatever wave. Windows: during warming (first start → release), then 7 and 30 days after release. A re-warm does NOT censor: the windows run on through the later wave and count what happened in it — censoring the accounts an operator chose to re-warm would bias survival upwards.
- Dead: a session dead for good (revoked, deactivated, banned, frozen — `account_frozen` cause) or an operator deletion as dead. Deleting a spam-blocked account is a write-off whatever reason was picked; a guessed or reasonless deletion is dead only with session evidence (`evidence`, or a `session_dead` row). Write-offs censor; a later `clean` or `spam_block_lifted` ends a block. `limited` and peer-flood quarantine are wounds; repeated `limited` is one.
- Death time: `extra.evidence_at` (first logged refusal after the last success/answer/life start), else the row's `created_at`, kept as `found_at`.
- Alive needs evidence: an answer (`clean` or `limited`) at or after the window's end, less one spam-status cache lifetime, because the probe at the window's end reuses a fresh cached answer. Without one the window is unchecked — shown apart, never in the denominator. Death during warming stays out of the release windows.
- Cohorts read the deciding wave (first released, else latest); `waves` groups lives by wave count. The swimmer has one row per life with ticks for later waves; `waves.load_account_waves` gives one account's history from the same stints.
- Probes: one lifespan task, one pass per interval, at most one account per pass, through the same cached `refresh_spam_status` the cycle uses. A pass first journals deaths no session check saw (an action marked the account frozen). Only the latest closed window asks; an unanswered probe waits out a retry period, and two accounts behind one proxy are never asked within the spacing. With none due, it re-asks one free spam-blocked account after `block_recheck_hours`. Pacing memory is in-process: one uvicorn worker.
- The SPA shows counts as «N из M»; a percentage only from ten decided accounts. Pure life/outcome/cohort functions stay free of I/O and the clock.

Verify: change the pure functions with synthetic-journal tests (unknown, written-off, repeated id, re-warm, life without an end); exact thresholds and intervals belong to config, tests and code, not memory.
