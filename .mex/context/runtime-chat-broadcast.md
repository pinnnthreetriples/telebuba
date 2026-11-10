---
name: runtime-chat-broadcast
description: Chat broadcast campaigns, joins into lists, chains, rounds and the board by chat.
last_updated: 2026-11-09
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

- One chat, one account. A group several accounts share gets one message. Each live
  account works its own queue in its own task for the whole run, in parallel — unlike
  neuroshilling's sequential pass, because chats are independent here and a staged
  dialogue is not.
- Rounds belong to the chat, not the campaign. A chat plays its chain, then rests on its
  own clock (the pause from the gear, drawn when its chain ends) and comes back for its
  next round by itself; the last round ends it as done. Nobody waits for the slowest
  chat, and there is no shared round or shared rest. `round_done` means "resting until
  its `next_action_at`", and the campaign's round is only a label: the most lagging
  unfinished chat's, and `rest_until` is the nearest end of a rest, set only while every
  unfinished chat rests (the board's `resting` phase). A chat that ended in an error
  comes back after its rest like a rested one; every other skip is final. Of the chats an
  account has ready, the earliest round goes first and ties by position, so a short rest
  never starves the chats at the end of the list. A new chat added on Continue starts at
  round 1.
- Work is balanced across the live accounts and moves to whoever is free. A scheduler
  pass, throttled and run at once when an account comes back, advances the chats whose
  rest is over (whoever owns them), re-places the chats of resting accounts by the time
  they have left, returns waiting ones, and then hands chats from the busiest account to
  the idlest until the loads differ by at most one, resting chats counting as load. Only
  an account that is live, not resting, not at its limit and active in the roster
  receives; a chat in flight (joining, captcha, writing, awaiting approval) is never
  taken, and a handover that races a worker is refused by the owner check on the worker's
  own write. A move prefers a chat the receiver is already in, then one nobody joined,
  and a balancing move does not mark the chat as handed over, so a later ban can still
  reroute it once. Fewer chats than accounts is fine: the extra accounts idle.
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
- An account Telegram stops is not lost: a flood wait, its own hourly/daily limit or a
  spam block RESTS it (`services/chat_broadcast/_rotation.py`) until a known moment — the
  flood's own wait, the second the oldest send leaves the limit's window, or hours for a
  spam block that @SpamBot is asked about at the end — and a waker brings it back. While
  it rests, a short rest keeps its chats, a medium one hands over the chats it has not
  entered, a long one hands over all, and only to accounts that still have room. The board
  shows the reason and the moment instead of a "writes in a minute" that keeps repeating.
- A ban arrives as the same class whether it is the spam block or one chat's ban, so
  @SpamBot is asked at once: a clean account loses only that chat, moved once to another
  account. Only a dead session (frozen, deactivated, logged out) leaves for good; Start and
  Continue give every other halted account a fresh try.
- Slow mode defers the chat. `ChatWriteForbiddenError` reads the chat's rights: a mute
  on this account alone moves the chat once, a chat closed to all is skipped. A chat
  refusing photos gets the text alone.
- A failure stays with its account. An unexpected exception in one account's task never
  ends the run: it is logged by type only, the task restarts after a pause, and a run of
  crashes halts that account as `worker_crash` and hands its chats over. An operator-set
  stop ("skip errors" off) and a revoked run still end the run for everyone.
- The run does not stall while an account can come back. An account another feature holds
  is claimed as soon as it frees up; one halted by errors in a row or a crash is returned
  to rotation after a pause, a few times in a row, and a successful send resets that
  count. With nobody live their chats wait for an account, the run stays `running` and
  the board says it waits, also after a process restart. A spam block and a dead session
  are never returned automatically, only by Start and Continue, which also reset the
  count. The run stalls only when no live, resting, claimable or retryable account is
  left and work remains, and it names the cause in `last_error`: `accounts_spam`, else
  `accounts_errors` (retries spent), else `accounts_dead`. If the only thing left is a
  wait for accounts held by another campaign or feature, it stalls with `accounts_busy`
  after `CHAT_BROADCAST__CLAIM_GIVE_UP_SECONDS` (default 3600). An explicit Start with no
  free account is still refused. Which accounts were banned or muted in a chat is kept
  in `chat_broadcast_targets.refused_by` (migration 111), so the balance never hands the
  chat back to them, also after a restart; `chat_kind` there tells the deleted-message
  check whether message ids are shared (megagroup) or per account (basic group).
- Settings cannot be saved while a run is attached: the run reads them once. The pauses
  are the exception (the board's gear): the run reads them live, and every chat's rest
  under way is redrawn from its own start, which only this process knows — after a
  restart it keeps its end. A chat that has already left its rest is not touched.
- The run ends only when no chat is left in play, checked together with the write that
  marks the campaign done; a chat kept from the board in the same instant is either
  returned to play or loses cleanly, never dropped. A volume stop (messages or time)
  ends the run in any state, also while every chat rests.
- A message's repeat count expands into steps of the chain, so a copy is just another
  step: its own journal key, its own pause, its share of the board's planned total.
- A deletion is an admin's signal the operator may overrule: a chat kept from the board
  still has its deletions recorded but is no longer skipped for them — only a ban stops
  it — and the choice outlives the run, because a fresh run lays its chats out anew. In a
  running or stopped campaign a keep puts the chat back in play in its own round; in a
  finished or not yet laid out one it only flags the chat.
- The board orders rows by importance once per load and keys expansion by chat, so a row
  the operator acts on does not slide away or hand its details to a neighbour.

Timings, caps and wording belong to config, code and tests, not here.
