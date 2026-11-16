---
name: runtime-spam-intel
description: The @SpamBot gateway, its shared probe budget, reply samples and the history sweep.
last_updated: 2026-11-16
edges:
  - target: context/runtime-warming.md
    condition: the warming cycle's spam step, the quarantine re-check or the hand-off check
  - target: context/runtime-neurocomment.md
    condition: ban confirmation or onboarding's spam probe
  - target: context/runtime-chat-broadcast.md
    condition: the rest-end spam check or a ban in one chat
  - target: patterns/add-log-event.md
    condition: adding or renaming a spam_intel_* event
---

# Spam Intel Runtime

`services/spam_intel/` is the only way to ask "what is this account's spam status": nothing else
imports the probe, the executor (`services/spam_status.py`) or `upsert_spam_status`
(`tests/test_architecture.py` is the rule). Design, evidence, owner decisions and the stage plan:
`docs/spam-intel.md`.

- A request is served from a cached reading inside the TTL (not when forced), else from a probe
  already in flight (single-flight per account, forced or not), else from the last real reading or
  failure younger than the coalescing window, and only then by a new probe if the budget allows. The
  budget is a pure function over rows in `spam_probe_attempts` / `spam_probe_state`, so it survives a
  restart: dead-session and no-proxy accounts are refused, then caps, backoff after an unreadable
  answer, per-proxy and fleet spacing, busy accounts, the operator's own limits.
- An answer the budget postponed or refused is an `unknown` verdict that says so (`served_from`,
  `retry_at`, `reason`; never stored). It is NOT an unreadable reading: warming's streak, the
  quarantine's counters and a broadcast's final halt must not count it. The caller's name travels in
  a context variable (`caller_scope`); a domain seam binds the feature that holds the account.
- The parser has a legacy classifier (unrecognised text is `clean`) and a strict one (`unknown`),
  chosen by `SPAM_INTEL__STRICT_PARSER`, off until real replies in the fleet's languages are captured:
  the executor keeps redacted, deduplicated reply samples and logs where the two disagree. A
  `restricted` flag is not a spam limit.
- The history sweep reads each eligible account's old @SpamBot dialog once, sends nothing, and is off
  unless switched on with the owner's go. `SPAM_INTEL__ENABLED=false` restores the pre-gateway path.
