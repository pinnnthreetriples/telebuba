---
name: runtime-warming
description: Warming scheduling, budget, dialogue, recovery and board runtime.
last_updated: 2026-10-15
edges:
  - target: patterns/account-devices.md
    condition: the warming device
  - target: context/architecture.md
    condition: layer boundaries, gateways or system design
  - target: patterns/add-warming-job.md
    condition: changing warming runtime work
  - target: patterns/change-survival-counter.md
    condition: survival of released accounts or the day-7/day-30 @SpamBot probes
  - target: patterns/change-warming-humanlike.md
    condition: visit cadence or kinds, account character, chat language or real account age
  - target: patterns/change-channel-pool.md
    condition: the channel pool search, its filters, the refill, self-search or the consent pull
grounds_to:
  - node: "function:0ef3f892444f3018e475bf85324f7f80"
    fingerprint: "mh:64:7b226d696e68617368223a5b31383134343139392c35353539363432352c38323430323834372c35373132303635362c3132333634393337352c34393933323837372c363633363132322c36333537323330352c31323732353739302c35313039363337352c31373931303839362c333937323038352c31373431353239352c35323931303230312c343831303535372c32373732313037342c31313638323639382c31383232353833342c35303631363336382c38303934333634362c37333737393438322c32363739393939392c313830393030342c393832393038372c373534363634362c3132373633353837372c34393638333433362c31303537373234392c32303732303730312c313634363838322c31333138303738312c3134363731353938312c343431393134332c3132373930323337382c353936373833322c36333931363138392c35313032393036322c31303836393338392c35363936323936392c353731383634332c32393637313335342c37363735343934312c3832363536312c36363138333530322c31393233333333312c343936303734382c32363234373734362c3130303431363439322c31363631353430392c32313834393432362c32383034353339322c35393638393230342c3135303339313533392c3135363131323331372c38313238383336302c32353239333438342c33393934363934322c313639363231362c34343930383434392c34383638383035342c32303036303231392c373834343332362c333235313338392c31313732363932355d2c226e65696768626f7273223a5b2266756e6374696f6e3a3565656362626137626636636532356138393535353738366334653530363337222c2266756e6374696f6e3a3566666261633732326539643132333835316334373762663761316663366466222c2266756e6374696f6e3a3762656237653036613731333130363164333930346135653535386332396133222c2266756e6374696f6e3a3865313230343761626539313138376531376534663765346538656434323931222c2266756e6374696f6e3a3937666165363132343064356436383635633534376165623137373334393634222c2266756e6374696f6e3a3939376435323736623862336566666536353563626465636165303065613865222c2266756e6374696f6e3a6130343565646139623539623235626564333466313132623936306232323535222c2266756e6374696f6e3a6566656132666131343738623666653464303364653032386136396163306234225d2c22746f6b656e436f756e74223a3230357d"
    bodyHash: "9aa4c0e1d5b3b62643e79b8f8ebe58b7c2a9020daaeb1835d02b19256811c919"
  - node: "function:95a0056c4fefdaf9c95e478351018bf5"
    fingerprint: "mh:64:7b226d696e68617368223a5b3336363839313938362c3133303036373630352c3736363436343735322c3231303735373830342c3132373334393133322c37393334343437342c363633363132322c3130333434373737352c3131373839373033322c3532363335323235342c33303538353835312c3132353037333139332c34323234323235342c35323931303230312c343831303535372c32373732313037342c3131333135353632322c3237343331333732312c3236343339383434322c38303934333634362c3239323136313337312c32363739393939392c313830393030342c3132383032363134372c3134353437393235372c3133333330373732352c3334383434343538322c3231333137393432312c3132393938373839302c34333636393037392c33313736313835342c32353738323639392c343431393134332c3134343331313935322c3636353530372c313632393331392c3131303731353836362c36303238383134312c35363936323936392c3133323233333533372c38323333373739302c3331333333333636322c3832363536312c32373634383931332c3235303536323032382c343936303734382c32363234373734362c3238333133393338372c3135303730303839302c32313834393432362c3135303938353731322c39313432393436322c3530303031353933352c3230383337353737342c38323339303030382c32353239333438342c3131343632323437362c3337353532323732332c3232333035303135312c3331313331363235362c32303036303231392c3632303632313036332c3237343435363036322c31313732363932355d2c226e65696768626f7273223a5b5d2c22746f6b656e436f756e74223a35317d"
    bodyHash: "0fedf9b63c992251a0042678c97916b00a9fd221ef1d089c25e0d56d3d5eec7c"
---

# Warming Runtime

- One owned in-memory `asyncio.Task` runs per persisted active account. Ownership is retained until the task is terminal; a timed-out Stop remains non-quiescent and blocks restart, promotion, handoff and deletion. FastAPI lifespan reconciles persisted state; one uvicorn worker is required while ownership is process-local.
- Persona controls cadence while phase/trust/readiness remain safety ceilings. Timing and caps come from config; persisted `next_run_at` survives restart, while an explicit stop/start may re-roll startup timing.
- Daily action budget is reserved before a cycle and reconciled afterward. The reservation is guarded by a per-booking token, not only generation or booked value, so a cancelled old cycle cannot release a newer booking. Each Telegram attempt is booked immediately before dispatch; cancellation or lease loss after dispatch is an unknown outcome and remains spent. Hard process death stays fail-closed: actual spend is unknown.
- The warming day is the account's local calendar day (phone tz, UTC when unknown): daily budget, quiet day, new-day wait and daily stats follow it; a parked account wakes in its own local morning at its chronotype. A stored day one ahead of the local date is the same day. Target counts worked days (worked cycle or quiet day, not error/quarantine). A start from idle («Вернуть в прогрев» too) opens the next `wave` from zero; a restart of a running stint keeps both.
- Cycle work spends one shared budget across online/join/read/react/story/DM actions; when tuning caps or cadence, keep later steps reachable.
- Extras are the last cycle step, drawn per persona from a registry of toggled-on eligible specs. Read extras never book the daily budget; write extras book it immediately before dispatch like every other step, and are skipped (never queued, never drawn) once it is spent. An extra that ran but found nothing to act on (`warm_skip`) stays booked yet is not a landed extra. Poll/video/voice extras aim only at posts the channel read classified as such.
- Self-scoped extras (notes, forwards, drafts, reminders) have no destination; core hardcodes `InputPeerSelf`.
- Media extras debit a per-cycle byte budget before dispatch; Premium-only extras never probe by writing — unknown Premium means "do not try".
- A channel warming left stays out of the cycle (no read, no re-join) for the re-join cooldown; `is_channel_joined` means joined-and-not-left, and a re-join restarts the joined-age clock the leave policy reads. Joins (re-joins too) obey a per-phase rolling-24h cap; past it an unjoined channel is skipped unread and unbooked.
- Inter-account DMs open `dm_min_age_hours` after the stint's `started_at`; cold peers resolve via `contacts.resolvePhone`, never saved as contacts. An unaddressable peer skips the turn without parking the sender, but still spends budget.
- Fleet DM breaker: DM spam blocks in a window pause all DMs (`dm_paused_until`; a lift logs the operator).
- @SpamBot is asked on a stint's first cycle, then every interval ± a per-account offset, in-cycle via the seam. A new `limited` quarantines; unreadable bumps a card-warning streak. Handoff needs `clean`: no answer refuses, `limited` un-promotes into quarantine.
- Quarantine releases on clean; still `limited` after `quarantine_max_repeats` stops in `spam_blocked`. A known block (`spam_blocked_at`) never re-quarantines, refuses or docks trust; DMs stay off; search/join/read/react stay on. A later `clean` lifts it (CAS on the mark, `spam_block_lifted`): parked goes idle; kept stays. Survival probes re-ask idle blocks.
- Scheduling lives in pacing/fleet modules, one session in cycle modules, task state/sleep/cancellation/recovery in runtime modules. A generation lease around Telegram dispatch stops a retired cycle starting a later action. Injectable collaborators stay behind the warming seam.
- Start refuses any account neurocomment already owns: the running listener and an in-flight channel-discovery run are hard exclusions; a live cooldown either runtime recorded is a health verdict and obeys the operator's `enforce_readiness` switch, like warming's own flood deadline does through trust. Refusals carry a stable code, never readiness reasons, and change only whether warming starts.
- A running neuroshilling campaign is refused the same ungated way, via the shared in-memory ownership registry, not a point check: warming claims each account it spawns and releases only on an identity match, and neuroshilling's start refuses against that claim — weaken either side and two paced streams share a session. Warming's re-spawn of an account it holds bypasses the refusal, safe only because refusal and eviction share the per-account lifecycle lock.
- Geo never refuses a start; consent is per `PHONE>PROXY` pair. Cards count settled actions.
- Board reads stay bulk-loaded; loop failures are logged and persisted.

Exact caps and timings belong to config/tests/code, not memory.
