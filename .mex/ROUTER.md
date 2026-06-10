---
name: router
description: Session bootstrap and navigation hub. Stable — does not track live state. Read at the start of every session before any task.
edges:
  - target: state/active.md
    condition: always — load current project state first
  - target: context/architecture.md
    condition: when working on system design, integrations, or layer rules
  - target: context/stack.md
    condition: when working with specific technologies or making tech decisions
  - target: context/conventions.md
    condition: when writing or reviewing code
  - target: context/decisions.md
    condition: when making an architectural choice or understanding why something is built a certain way
  - target: context/setup.md
    condition: when setting up the dev environment or running the project
  - target: context/telegram.md
    condition: when touching Telethon or any Telegram interaction
  - target: context/warming.md
    condition: when working with scheduled warming jobs or APScheduler
  - target: context/logging.md
    condition: when emitting, querying, or displaying log entries
  - target: context/kanban.md
    condition: always at session start — protocol for the GitHub Project board (pick work, move issues)
  - target: patterns/INDEX.md
    condition: when starting a task — check the pattern index for a matching pattern
last_updated: 2026-06-10
---

# Session Bootstrap

If you have not already read `AGENTS.md`, read it now — project identity, stack, file map, non-negotiables, commands.

Then read this file fully, then load `state/active.md` for the live picture of what works, what is missing, and known issues.

## Routing Table

| Task type | Load |
|-----------|------|
| Current project state (always first) | `state/active.md` |
| Picking work / moving board items (always at session start) | `context/kanban.md` |
| Understanding how the system works | `context/architecture.md` |
| Working with a specific technology | `context/stack.md` |
| Writing or reviewing code | `context/conventions.md` |
| Making a design decision | `context/decisions.md` |
| Setting up or running the project | `context/setup.md` |
| Anything Telethon / Telegram-related | `context/telegram.md` |
| Anything scheduled / warming-related | `context/warming.md` |
| Anything log-related (file / SQLite table / Logs page) | `context/logging.md` |
| Any specific task | check `patterns/INDEX.md` for a matching pattern |

## Behavioural Contract

**Session start (run once before any task):** load `state/active.md` and `context/kanban.md`. Report what is in `In progress` and `Ready` on the board. Pick work per the kanban protocol.

For every task, follow this loop:

1. **CONTEXT** — Move the board item to `In progress` (kanban Step 3). Load the relevant context file(s) from the routing table above. Check `patterns/INDEX.md` for a matching pattern. If one exists, follow it. Narrate what you load: "Loading architecture context..."
2. **BUILD** — Do the work. If a pattern exists, follow its Steps. If you are about to deviate from an established pattern, say so before writing any code — state the deviation and why.
3. **VERIFY** — Load `context/conventions.md` and run the Verify Checklist item by item. State each item and whether the output passes. Do not summarise — enumerate explicitly.
4. **DEBUG** — If verification fails or something breaks, check `patterns/INDEX.md` for a debug pattern. Follow it. Fix the issue and re-run VERIFY.
5. **GROW** — After meaningful work, run this binary checklist:
   - **Ground:** What changed in reality? Name the changed behavior, system, command, dependency, or workflow.
   - **Record:** Update `state/active.md` (Working / Not Yet Built / Known Issues). Update any `context/` file whose facts changed.
   - **Orient:** If this task can recur and no pattern exists, create one in `patterns/` using `patterns/README.md`, then add it to `patterns/INDEX.md`.
   - **Write:** Bump `last_updated` in every scaffold file you changed. Run `mex log --type decision "..."` when the why matters.
   - **Board:** Move the issue to `In review` when the PR is open, and to `Done` after the merge. See `context/kanban.md` for the exact commands.
