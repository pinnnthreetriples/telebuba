---
last_updated: 2026-08-17
edges:
  - target: context/runtime-warming.md
    condition: the other writer of the account-ownership registry
  - target: context/runtime-neurocomment.md
    condition: the shared join budget or the listener exclusion
  - target: patterns/add-log-event.md
    condition: adding or renaming an operator-facing event
---

# Neuroshilling Runtime

A campaign replays one approved multi-account dialogue into target chats; `revive`
replays it round and round in one chat the operator owns.

- An account may be ASSIGNED to any number of campaigns but HELD by at most one running one, and never while warming or a neurocomment campaign holds it. One in-memory registry answers that, keyed by an identity (the run or campaign holding it) rather than a flag, so a late callback from an evicted generation cannot release the claim its successor now holds.
- The neurocomment listener is deliberately not a holder in that registry. Enrolling it would mean a claim, a release and a restart-time restore inside a runtime whose restart logic is load-bearing, so the exclusion is paid as point checks against the listener's own durable state, in both directions, instead. That is a trade rather than a claim that point checks are better; the next feature needing the answer is the one that should stop paying it.
- The rolling daily join budget is counted out of neurocomment's join log rather than a private counter, because Telegram counts joins per ACCOUNT and does not care which of our features spent them. Two private counters let one account join twice its cap with both features certain they had stayed under it. The price — neurocomment reaches its own cap sooner while a campaign runs — is the point, not a side effect.
- Text de-duplication is scoped to the target, never global: replaying ONE dialogue into many chats is the feature, so a global reservation would pass the first target and refuse every other as a duplicate of it. Scoped, the gate still fires on the real signal — the same words twice in the same chat. `revive` narrows it once more, to the cycle, because saying the same lines into the same chat again IS that mode rather than an accident of it; without that the second cycle publishes nothing for the length of the dedup window.
- Replies to live strangers are bounded by parsing the string that would be SENT, not by instructions in the prompt. Delimiter defences are documented to fall to adaptive attacks, so the fence is depth and the parser is the boundary. Refusals travel as stable codes because a log event's `extra` reaches an HTTP body, so no attacker-controlled text may enter one.
- The outbound content filter is warming's, and its stock forbidden words are the vocabulary a shilling dialogue is written in. There is no neuroshilling copy of that list on purpose — the two features must not disagree about what may go out — so unblocking a campaign is an operator edit to warming's settings. The same filter is asked again at APPROVAL, not only before each send, so the operator learns at once instead of watching a run finish `done` having skipped every message step.
- Stop bumps the run generation and cancels; a status row is not a stop, because a coroutine asleep in a step delay never reads rows. A resumed run keeps its STORED run id: a fresh one would face an empty journal and replay the whole dialogue into chats that already have it.
- Parallel mode is refused on the server as a decision, not an omission — it turns joins into a volley and quota re-counts into a race for nothing a sequential pass does not already give.

Caps, timings, prompt wording and the refusal-shape catalogue belong to config, code
and tests, not here.
