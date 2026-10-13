---
name: change-survival-counter
description: Change the survival counter of warmed accounts, its outcome rules or the day-7/day-30 @SpamBot probes.
last_updated: 2026-10-13
edges:
  - target: context/runtime-warming.md
    condition: warming stints, graduation, quarantine or the @SpamBot schedule
  - target: context/architecture.md
    condition: layer boundaries, gateways or system design
---

# Change Survival Counter

- Reads only the lifecycle journal: a stint is one warming run of one account life. Lives split at each `deleted` row, and the board also drops rows older than the live account row, because the deletion row is best-effort and a re-imported id must not wear a past life. A stint opens on a new stint start, never on a resume that carried the clock.
- Release is the first graduation (target reached or the operator's promotion). Windows: during warming, then 7 and 30 days after release.
- Dead: @SpamBot `limited`, a session dead for good, or a deletion as dead; an old deletion without a reason is dead only if @SpamBot limited that life (Q11). A write-off, or a fresh stint of the same life, censors: out of the count, not a death. A peer flood without a limit is a wound: alive, counted beside it (Q7).
- Alive needs evidence: a clean answer at or after the window's end, less one spam-status cache lifetime, because the probe at the window's end reuses a fresh cached answer. Without one the window is unchecked — shown apart, never in the denominator. Death during warming stays out of the release windows.
- Probes: one lifespan task, one pass per interval, at most one account per pass, through the same cached `refresh_spam_status` the cycle uses — no second Telegram path. Only the latest closed window asks, so a life asks at most twice; an unanswered probe waits out a retry period, and two accounts behind one proxy (no proxy = the host's address) are never asked within the spacing. Pacing memory is in-process: one uvicorn worker.
- The SPA shows counts as «N из M»; a percentage only from ten decided accounts, and the 30-day count heads the tile only once it has that many. Pure stint/outcome/cohort functions stay free of I/O and the clock.

Verify: change the pure functions with synthetic-journal tests (unknown, written-off, repeated id, stint without an end); exact thresholds and intervals belong to config, tests and code, not memory.
