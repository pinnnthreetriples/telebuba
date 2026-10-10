---
name: runtime-user-parser
description: User parser runs, bases, presets and exports.
last_updated: 2026-10-09
edges:
  - target: context/runtime-discovery.md
    condition: the reservation and per-account stream model it copies
  - target: context/runtime-chat-broadcast.md
    condition: the shared join budget and the "already inside" rule it borrows
  - target: patterns/add-log-event.md
    condition: adding or renaming a user_parser_* event
---

# User Parser Runtime

Operator-started background runs that collect people from groups and channels in three
modes (members, message authors, commenters) and save every run as a base («Базы»).

- Reservation is discovery's: one fleet snapshot, warming's lifecycle lock for every
  picked account in sorted order, every holder asked again inside it, then all accounts
  claimed in the account-owner registry or none. A refusal is a status naming the first
  account, never an exception. The parser is a registry owner, so warming, the listener,
  the comment engine and discovery all refuse an account it holds, and it refuses theirs.
- One paced stream per account over one shared queue; a job is one page. A flood up to
  the configured sit-out ceiling is waited out and the account comes back; a longer one,
  repeated failures, a cooldown another feature set, or a client that never connects
  take the account out for the run. A page a flood or a dead connection answered nothing
  about is retried once on whoever takes it next. The run ends when the queue is empty
  or no account is left; floods are written to the fleet cooldown map so every feature
  sees them.
- Failure classes come from the gateway's `kind` only. A hidden member list, an invite
  that cannot be joined, and Telegram handing out fewer members than it reports are
  per-source statuses; the run goes on. ~10 000 members is an observation, so a short
  list is "may be incomplete", never a promise of the whole group.
- Private invite chats are joined automatically, but only after asking whether the
  account is already inside: Telegram answers a re-join with plain success, which would
  spend the shared join budget for nothing. Joins go through the shared join lock, the
  fleet join log and the per-account cap, like chat broadcast.
- Message text crosses the gateway only to match keywords and measure comment length;
  it is never stored. People carry public profile facts only — no phone, no
  access_hash, no bio.
- A message either counts toward its author when it is read (replies, forwards,
  keywords, comment length) or not; a person is kept or dropped once the run is over.
  Sources and message counts measure only counted messages, so "at least N sources"
  respects the keywords.
- Every ending saves what was collected: done, every account gone (`failed`), Stop
  (`stopped`), shutdown (`interrupted`). A process that died mid-run leaves a `running`
  row that startup marks `interrupted`; v1 never resumes a run. Live progress is in
  memory only.
- Bases are never deleted automatically; only the operator deletes one, with its people.
  Export is CSV (UTF-8 with BOM, so Excel opens it) and JSON, streamed in chunks; an
  Excel file waits for an operator decision on a new dependency.
- The base's default name is worded by the modal in the operator's language; the server
  only falls back to a locale-neutral one.

Limits, pauses and wording live in config, code and tests, not here.
