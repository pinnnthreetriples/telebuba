---
name: runtime-chat-broadcast
description: Chat broadcast campaigns, joins into lists, chains, rounds and the board by chat.
last_updated: 2026-11-10
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
are not in) or into the groups they already are in. The run lifecycle copies
neuroshilling's (claim, run id, fenced seams, journal, restart resume).

- One chat, one account. A group several accounts share gets one message. Each live
  account has its own long-lived worker task; a crash stays with its account (logged by
  type only, restarted after a pause, repeated crashes halt it as `worker_crash`).
- Rounds belong to the chat, not the campaign. A chat plays its chain, then rests on its
  own clock and comes back for its next round by itself; the last round ends it as done.
  Nobody waits for the slowest chat. `round_done` means "resting until `next_action_at`";
  the campaign's `round` is only a label and `rest_until` is set only while every
  unfinished chat rests (the board's `resting` phase). An error-skipped chat returns
  after its rest, every other skip is final. A new chat added on Continue starts at
  round 1.
- Work is balanced across live accounts. A throttled scheduler pass (at once when an
  account comes back) promotes chats whose rest is over, re-places the chats of resting
  accounts, returns waiting ones and hands chats from the busiest account to the idlest
  until loads differ by at most one. A chat in flight (joining, captcha, writing,
  awaiting approval) is never taken; a handover racing a worker is refused by the owner
  check on the worker's own write. Chats the receiver must join are not given to an
  account at its join cap, and a chat is never given back to an account that was banned
  or muted in it: `chat_broadcast_targets.refused_by` keeps that across restarts.
- Accounts another feature drives are left out; Start refuses only when none is free.
- Stop, a stall and a failure continue with the SAME run id; only a finished campaign
  mints a new one. The journal row is written `pending` before every send: an occupied
  key is a step already played, a dead process's `pending` becomes `unconfirmed` and is
  never resent. A refusal that never reached the chat (spam block, slow mode, dropped
  connection, not a member) gives the step back.
- Joins spend the fleet's one join budget under the shared join lock. An account already
  inside a chat never joins it again; a rejected join request cannot be told from a
  pending one, so a request ends by the operator's timeout.
- The AI rewrite is an embellishment, never a gate: any failure or unsafe answer sends
  the operator's text.
- An account Telegram stops is rested, not lost (`services/chat_broadcast/_rotation.py`):
  a flood wait, its own limit or a spam block rests it until a known moment, a waker
  brings it back, and its chats stay, are handed over or are all handed over by the
  length of the rest. A ban is asked to @SpamBot at once: a clean account loses only
  that chat, moved once. Only a dead session leaves for good.
- The run does not stall while an account can come back. A busy account is claimed as
  soon as it frees up; one halted by errors in a row or a crash is returned after a
  pause, a few times, and a successful send resets the count. A spam block and a dead
  session return only by Start and Continue. The run stalls only when nobody is live,
  resting, claimable or retryable, and names the cause in `last_error`: `accounts_spam`,
  `accounts_errors`, `accounts_dead`, or `accounts_busy` (only accounts held elsewhere,
  after `CHAT_BROADCAST__CLAIM_GIVE_UP_SECONDS`).
- Supervisor and scheduler faults are logged and retried, never charged to an account.
- Settings cannot be saved while a run is attached, except the pauses: they are read live
  and the rests under way are redrawn from their own start.
- The run ends only when no chat is left in play, checked together with the write that
  marks the campaign done, so a chat kept from the board at that instant is never
  dropped. A volume stop (messages or time) ends the run in any state.
- A deletion is an admin's signal the operator may overrule: a kept chat still records
  deletions but is skipped only for a ban. `chat_kind` tells the deletion check whether
  message ids are shared (megagroup) or per account (basic group).
- The board orders rows once per load and keys expansion by chat, so a row the operator
  acts on does not slide away.

Timings, caps and wording belong to config, code and tests, not here.
