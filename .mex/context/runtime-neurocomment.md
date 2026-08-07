---
last_updated: 2026-08-07
---

# Neurocomment Runtime

- A persisted listener choice watches active campaign channels; each post is handled by a tracked task. Warming and listener roles remain mutually exclusive for an account.
- Pipeline: campaign/filter → healthy under-quota account → atomic post claim → generate/deduplicate → delay → comment → persist. Atomic claims are the duplicate-send fence.
- Cooldown/ban/access/challenge outcomes write durable pair/account state. Unknown terminal send failures fail closed instead of making the same pair immediately eligible again. Cooldowns clear only by expiry.
- Selection is candidate-scoped and prefers the least-busy eligible account with randomized ties; hot-path quota/readiness queries must not scan the whole fleet/history.
- Channel joins are paced and bounded by the persisted rolling neurocomment join budget. Already-participant no-ops do not spend that join count; warming joins are a separate domain.
- Listener reconciliation publishes watched/unwatched state atomically. A join pass runs single-flight in the background and re-subscribes after joins drain so newly resolvable channels become watched.
- Captcha handling distinguishes chat restrictions from bot challenges. The captcha queue holds NO operator control: a listed row claims the automatic rule is working that pair right now, and every exclusion exists to keep that claim true. The board badge answers a different question and may legitimately differ. Exclusions belong in the one SQL statement, because `LIMIT` is applied by the database, not by filtering the page afterwards.
- Terminal verdicts are reached without an operator and are one-way: a pair the guardian bot blocks gets exactly ONE authorised re-solve and then gives up and leaves the discussion group; a pair that spends its re-join budget leaves and is reported once; a channel whose comments are switched off is unlinked as soon as either surface reads that verdict. A channel leaves the campaign only once every serving account has finished. Nothing deletes a readiness row, which is what keeps all of this one-way.
- Every such budget is a persisted stamp on the pair, spent BEFORE the pass that would use it — never a count of attempt rows, which count every trigger and never move for a pair the pass cannot reach. A rule that walks an account out of a chat must exempt a pair still owed an attempt (the shared rolling-24h join cap is the usual reason a pass never arrived) and a terminal address the leave would fail on anyway, and must write its "already reported" mark conditionally: the review acts on a snapshot, so a pair re-admitted mid-tick must not be marked and walked back out.
- A probe's own state is not always evidence: the ban probe reports comments-disabled whenever the linked group merely fails to resolve, so acting on it directly would unlink a live channel and destroy per-account pins nothing restores. Re-read through the authoritative resolve and let one function both report and act, so no surface can report without acting.
- The operator's "clear logs" delete is prefix-scoped and unbounded in time. It writes an audit row AFTER the delete under a code carrying no domain prefix, so the next press cannot erase the evidence; the count endpoint answers the same clause the delete uses, so the number confirmed is the number that goes.
- Append-only comments/challenges/join logs are pruned by configured retention. Retention must not invalidate the rolling join window or delete in-flight/cache rows that still carry behavior.
- Incoming posts have no durable queue/catch-up, and send↔DB reconciliation is not durable; changing that requires a persistence design rather than another in-process task.
- File splits, exact thresholds and transient performance measurements belong to executable gates/tests, not memory.

Discovery has its own route in `runtime-discovery.md`.
