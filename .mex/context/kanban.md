---
name: kanban
description: GitHub Project (Kanban) workflow protocol. Every coding session reads this on start, picks work from the board, and moves issues as state changes.
triggers:
  - "kanban"
  - "board"
  - "project"
  - "issue"
  - "what should I work on"
  - "what's next"
  - "in progress"
  - "ready"
edges:
  - target: state/active.md
    condition: when board state interacts with live project state
  - target: context/conventions.md
    condition: when picking a task — load coding rules before starting
  - target: patterns/INDEX.md
    condition: when starting work on a board item — check for a matching pattern first
last_updated: 2026-06-16
---

# Kanban Workflow

The project board is the single source of truth for "what should I work on next". Every coding session starts by reading the board and ends with the board reflecting the new reality.

## Board

- **Owner:** `pinnnthreetriples`
- **Project number:** `2`
- **Name:** `telebuba`
- **URL:** https://github.com/users/pinnnthreetriples/projects/2

### Columns (Status field)

| Column | Meaning |
|---|---|
| `Backlog` | Captured but not yet ready to work on — needs scoping or blocked. |
| `Ready` | Fully scoped, agent can pick this up without further input. |
| `In progress` | Currently being worked on. **At most one item per session.** |
| `In review` | PR opened, waiting on merge. |
| `Done` | Merged. Closed issue. |

### Other fields

- **Priority:** `P0` / `P1` / `P2` — when auto-picking from `Ready`, take the highest priority first.
- **Size:** `XS` / `S` / `M` / `L` / `XL` — informational, the agent does not split work based on this.

## Session Protocol

### Step 1 — At session start, read the board

```bash
"D:/gh.exe" project item-list 2 --owner pinnnthreetriples --format json
```

Report to the user:
- What is in `In progress` (any abandoned work?).
- What is in `Ready` (work the agent can pick up now).

### Step 2 — Pick work

The agent is allowed to **promote items from `Backlog` → `Ready` itself**, then pick from `Ready`. The promotion is not automatic — it requires the agent to judge that the item is actionable.

#### 2a. Try `Ready` first

- If `Ready` already has items, list them sorted by `Priority` (P0 → P2) and either:
  - take the issue the user named (`"do #12"`), or
  - propose the top-priority `Ready` item and start once confirmed.

#### 2b. Otherwise, scope from `Backlog`

If `Ready` is empty (or the user explicitly says "look at backlog"), walk `Backlog` items and decide for each: **is this item actionable in a single session without further user input?**

An item is **actionable** if all of these hold:
- It has a clear "done when…" — either written in the body, or unambiguously inferable from the title plus our context files.
- It does not depend on an unresolved `Open Decision` in `state/active.md`.
- It does not depend on another open issue that is still `Backlog` / `In progress`.
- It fits in roughly one session of work (rough size ≤ `L`).

For each actionable item:
- Move it to `Ready` (option id `61e4505c`) with a one-line comment on the issue: `Promoted to Ready: <one-line rationale>`.
- If `Priority` is unset, set it (`P0` only if blocking, default `P2`).

For non-actionable items, leave them in `Backlog` and surface the blocker to the user: "`#N` needs `<X>` decided / `#M` merged first — leaving in Backlog."

Then run 2a with the freshly populated `Ready`.

#### 2c. Empty board

If both `Ready` and `Backlog` are empty, tell the user. Do not invent work.

### Step 3 — Move to `In progress`

Run the move command from "Common Commands" below with `--single-select-option-id 47fc9ee4`. Confirm to the user: "Moved #N to In progress".

### Step 4 — Do the work

- Create a branch named `<type>/<issue-number>-<short-slug>` (e.g. `feat/12-account-creation`, `fix/9-floodwait-backoff`).
- Reference the issue in every commit message: `... (#12)` or `Refs #12`.
- Follow `context/conventions.md` and any matching `patterns/` runbook.

### Step 5 — Open PR → `In review`

When the PR is opened:
- The PR description should include `Closes #N` so the issue auto-closes on merge.
- Move the project item to `In review` (option id `df73e18b`).

### Step 6 — Merge → `Done`

On merge:
- GitHub auto-closes the linked issue.
- Move the project item to `Done` (option id `98236657`). The agent does this explicitly — do not rely on auto-archive.

## Constant IDs (do not edit unless the project is recreated)

```
PROJECT_ID         = PVT_kwHOBW56Bs4BaS-m
STATUS_FIELD_ID    = PVTSSF_lAHOBW56Bs4BaS-mzhVLloY

STATUS_BACKLOG     = f75ad846
STATUS_READY       = 61e4505c
STATUS_IN_PROGRESS = 47fc9ee4
STATUS_IN_REVIEW   = df73e18b
STATUS_DONE        = 98236657
```

## Common Commands

### List the whole board (JSON, parseable)

```bash
"D:/gh.exe" project item-list 2 --owner pinnnthreetriples --format json
```

### Find a board item id for a given GitHub issue

The item id is **not** the issue number — it is the project-internal id. Look it up with:

```bash
"D:/gh.exe" project item-list 2 --owner pinnnthreetriples --format json \
  | python -c "import sys, json; d=json.load(sys.stdin); print(next(i['id'] for i in d['items'] if i.get('content', {}).get('number') == ISSUE_NUMBER))"
```

Replace `ISSUE_NUMBER` with the actual `#N`.

### Move a board item to a new status

```bash
"D:/gh.exe" project item-edit \
  --id <ITEM_ID> \
  --field-id PVTSSF_lAHOBW56Bs4BaS-mzhVLloY \
  --project-id PVT_kwHOBW56Bs4BaS-m \
  --single-select-option-id <STATUS_OPTION_ID>
```

Pass one of the `STATUS_*` constants above as the option id.

### Add a new issue and immediately put it on the board

```bash
"D:/gh.exe" issue create --repo pinnnthreetriples/telebuba \
  --title "..." --body "..." \
  --project telebuba
```

(`--project telebuba` adds it to project #2 in `Backlog` by default.)

### Leave the promotion-rationale comment when moving Backlog → Ready

```bash
"D:/gh.exe" issue comment <ISSUE_NUMBER> --repo pinnnthreetriples/telebuba \
  --body "Promoted to Ready: <one-line rationale>"
```

## Rules

- **One `In progress` item per session.** If something is already there from a previous session, surface it and ask before starting something new.
- **Move on time, not in batches.** Move to `In progress` *before* starting work; move to `In review` *when the PR is opened*, not later.
- **Never close an issue without merging a PR.** If the work turns out to be wrong / unneeded, move back to `Backlog` with a comment, do not delete.
- **Refresh the board on session resume.** State printed at the start of a session can be stale by the time the agent acts — re-list before moving.
- **Promoting Backlog → Ready is a judgement, not a mass action.** Promote one item at a time, with a one-line rationale comment. Never batch-promote the whole backlog.
- **If the user says "just do something" with an empty `Ready`, the agent scopes from `Backlog` first.** It does not silently invent tasks outside the board.

## What does NOT belong on the board

- Routine maintenance (dependency bumps, formatting cleanups, scaffold edits) — these go through the normal commit flow, no board item required.
- "Ideas to consider later" — those live in `state/active.md` → Open Decisions, not in `Backlog`. Backlog is for things that will get done, just not yet.
