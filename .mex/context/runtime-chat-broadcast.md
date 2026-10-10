---
name: runtime-chat-broadcast
description: Chat broadcast campaigns, joins into lists, chains, rounds and the board by chat.
last_updated: 2026-10-08
edges:
  - target: context/runtime-neuroshilling.md
    condition: the run lifecycle it copies, or the account-ownership registry
  - target: context/runtime-neurocomment.md
    condition: the shared join budget or the captcha solver it borrows
  - target: patterns/add-log-event.md
    condition: adding or renaming a chat_broadcast_* event
---

# Chat Broadcast Runtime

Selected accounts write a chain of messages into groups: from a list (joining what they
are not in) or into the groups they already are in. The UX was agreed on a mockup with the
operator; the run lifecycle copies neuroshilling's (claim, run id, fenced seams, journal,
restart resume).

- One chat, one account. A group several accounts share gets one message. Each account
  works its own queue in its own task, in parallel — unlike neuroshilling's sequential
  pass, because chats are independent here and a staged dialogue is not.
- Accounts another feature drives are left out, not refused: the board names who holds
  them, and Start refuses only when none is free.
- Stop, a stall and a failure are continued with the SAME run id; only a finished campaign
  mints a new one and lays its chats out again. The journal row is written `pending`
  before every send: an occupied key is a step already played, a dead process's `pending`
  becomes `unconfirmed` and is never resent. Continue plays the chain as saved now: a chat
  whose step lies past a chain shortened since Stop is through for that round. A refusal
  that never reached the chat (spam block, slow mode, dropped connection, not a member)
  gives the step back, so the account that takes the chat over sends it.
- Joins spend the fleet's one join budget under the shared join lock, because Telegram
  counts joins per account whatever feature spends them. A rejected join request cannot
  be told from a pending one, so a request ends by the operator's timeout.
- An account already inside a chat never joins it again nor waits the post-join pause:
  Telegram answers a re-join of a public chat with plain success, so membership is asked
  before joining, and a new run keeps each chat with the account its last run left inside
  (a stale one surfaces as "not a member" on the first send and joins normally).
- Saved chat lists («категории») are copied into a campaign's settings when picked, never
  linked: a campaign stays what was saved for it.
- The guardian-bot solver is neurocomment's, reused through a public export; its audit rows
  and feed lines stay neurocomment's.
- The AI rewrite is an embellishment, never a gate: no key, the day's budget spent, an
  error, or an answer that drops or invents a link or mention, uses a forbidden word or
  balloons — the operator's text goes out. Links are the offer, so the warming link filter
  is not applied to them; only its forbidden words are.
- An account at its hourly/daily limit is checked before it joins anything, and hands only
  chats it has not entered to accounts that still have room — never back and forth.
- Settings cannot be saved while a run is attached: the run reads them once. The pauses
  are the exception (the board's gear): the run reads them live, and a rest under way is
  redrawn from its start, which only this process knows — after a restart it keeps its end.
- A message's repeat count expands into steps of the chain, so a copy is just another
  step: its own journal key, its own pause, its share of the board's planned total.
- A deletion is an admin's signal the operator may overrule: a chat kept from the board
  still has its deletions recorded but is no longer skipped for them — only a ban stops
  it — and the choice outlives the run, because a fresh run lays its chats out anew. A keep
  holds only on the campaign moment it was decided on; past its round's end it just flags
  the chat, and opening the next round (one write with the round itself) re-queues it.
- The board orders rows by importance once per load and keys expansion by chat, so a row
  the operator acts on does not slide away or hand its details to a neighbour.

Timings, caps and wording belong to config, code and tests, not here.
