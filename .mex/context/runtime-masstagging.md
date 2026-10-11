---
name: runtime-masstagging
description: Masstagging campaigns — story mentions and priming through the auto-delete toggle.
last_updated: 2026-10-11
edges:
  - target: context/runtime-chat-broadcast.md
    condition: the run lifecycle it copies, or the account-ownership registry
  - target: context/runtime-telegram.md
    condition: the story and auto-delete gateway actions, or per-account session ids
  - target: patterns/add-log-event.md
    condition: adding or renaming a masstagging_* event
---

# Masstagging Runtime

Selected accounts make people look at their profile. Two modes, agreed on a mockup with
the operator: a story whose caption mentions up to N people (each gets "mentioned you in
a story"), or priming — no post at all, only the DM's auto-delete timer switched on and
off, which leaves two service lines in the person's chat. The run lifecycle is the chat
broadcast's: claim the free accounts, a run id, a generation fence, settle once, Pause and
a failed run continue the same run, a restart resumes it.

- The dialog first. With "open a dialog first" on (the default), the account primes the
  person before the story: a mention in a chat that does not exist may never push. A
  primed person waits for the story of the account that opened the dialog.
- A spam-blocked account never works: it can still post, but its mentions push nobody.
  The verdict is the account's stored spam status — no new probe; a peer flood mid-run
  writes that verdict and takes the account out. A dead session leaves the run too.
- Without Premium an account gets the free ceiling of stories a day (a setting, because
  nobody could confirm Telegram's number), five mentions per story, a 24 h lifetime, a
  200-character caption and no link. The link area is a bare rectangle in the API; on a
  photo its plate is drawn into the picture.
- Limits are per account and day, whatever campaign spends them; the day starts at 09:00
  local time. A day spent rests the campaign until then and it goes on by itself; floods
  in a row rest that account; every account flooded out stops the run for the operator.
- "Skip those already tagged" reads one registry across all campaigns, by Telegram user
  id; deleting a campaign keeps it, because those people were tagged regardless.
- An id is per account session: a person is resolved by the account that tags them, a
  contact or a nameless group member belongs to the account that found them, and a phone
  is found only among that account's contacts — never imported.
- A story handed to Telegram is marked before the call; if the answer is lost or the
  process dies, those people end unconfirmed and are never mentioned again by that run.

Timings, caps and wording belong to config, code and tests, not here.
