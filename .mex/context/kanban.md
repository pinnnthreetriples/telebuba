---
name: kanban
description: GitHub Project #2 workflow.
triggers: [kanban, board, project, issue, ready, in progress]
edges:
  - target: context/conventions.md
    condition: start implementation
  - target: patterns/INDEX.md
    condition: pick matching runbook
last_updated: 2026-07-16
---

# Kanban
Board: owner `pinnnthreetriples`, project `2`, repository `pinnnthreetriples/telebuba`.

Status flow: `Backlog → Ready → In progress → In review → Done`.

## Protocol
1. Refresh the board; surface existing `In progress` work.
2. Use the user-named issue or highest-priority actionable `Ready` item. Do not invent work.
3. Move one item to `In progress` before coding.
4. Create a task branch, follow context/patterns, and reference the issue.
5. Open a PR with `Closes #N`; move the item to `In review`.
6. After merge, move it to `Done`. Never close work without a merged PR.

Promote `Backlog` to `Ready` only when scope, completion criteria, dependencies, and decisions are clear; add a short rationale comment.

## Project IDs
```text
PROJECT_ID=PVT_kwHOBW56Bs4BaS-m
STATUS_FIELD_ID=PVTSSF_lAHOBW56Bs4BaS-mzhVLloY
Backlog=f75ad846 Ready=61e4505c In_progress=47fc9ee4 In_review=df73e18b Done=98236657
```

Use `gh project item-list 2 --owner pinnnthreetriples --format json` and `gh project item-edit ...`; never hardcode a machine-specific executable path. Routine dependency/docs/scaffold maintenance does not require a board item.