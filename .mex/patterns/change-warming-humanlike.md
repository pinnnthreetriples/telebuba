---
name: change-warming-humanlike
description: Change how a warming account behaves like a person — visit cadence and kinds, character and chat manner, chat language, real account age, pool fill, discussions, groups and live DMs.
last_updated: 2026-10-13
edges:
  - target: context/runtime-warming.md
    condition: warming scheduling, budget, dialogue or board runtime
  - target: context/architecture.md
    condition: layer boundaries, gateways or system design
---

# Change Warming Human-Like Behaviour

- Cadence: the persona decides the visits a day; the phase cap only bounds them by the smallest worthwhile visit, and the day's remainder is split over the visits left. The minimum-two-action gate stays the floor of every visit budget. The board's cadence preview is the same math, so the start dialog quotes what the engine plans with.
- Visit kinds: glance, normal, binge (never before the configured phase), reply-only (only while a partner's DM waits and DMs are allowed; it skips the persona's DM roll, never a DM gate). The kind's budget caps the cycle request; anything unspent returns through the reservation reconcile, never a second booking.
- Order: only reads (story glance, one free read extra) may precede the channels; writes never do. The visit's step list is stored fenced by the booking token, and progress is monotonic along it — add a new step to the plan, or the card never shows it.
- Character: code draws language, country, city, age and gender from the fleet hash; the model fills only occupation and interests in JSON mode, and a profile that repeats another (same occupation, interests overlap at the cap) is asked again and not stored. The manner is drawn from the account id and applied after sanitising, before the filter and dedup.
- Language: prompts are English instructions naming the phone's language; dialogue pairs only accounts with a common language, and a cross-language pair forces a rebuild.
- Real age: only the operator's registration date counts (the id estimate is a UI hint). It shifts the DM opening and the phase age; day one and the trust ceiling always apply.
- Pool fill: searches read with an idle account outside warming, through gateway reads only (no neurocomment campaign runtime). Finds are `pending` rows; nothing walks or counts them until the operator approves them, and typing a proposed channel approves it.
- Discussions and groups: a comment thread is read on the channel post itself, never by joining its linked group. Groups are walked only from their phase on, reacted to less, never written in; their joins share the per-phase join cap.
- Live DMs: reactions, stickers and forwards run inside the dialogue turn, after every DM gate, with the fleet pause re-read before the send. Media waits for a few exchanges in the pair, and a variant that lands nothing leaves the message for a later visit.

Verify: the warming conftest neutralises the budget split, the visit order draws, the character and the manner for whole-cycle scenarios — test those in their own modules, and keep a whole-cycle test per kind.
