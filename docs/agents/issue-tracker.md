# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues in `pinnnthreetriples/telebuba`. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..." --project telebuba`. Use a heredoc for multi-line bodies. The `--project telebuba` flag is **required**: it drops the issue onto GitHub Project board #2 (`Backlog` column), this repo's single source of truth for "what to work on next" (see `.mex/context/kanban.md`). An issue created without it bypasses the board.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v` — `gh` does this automatically when run inside a clone.

## When a skill says "publish to the issue tracker"

Create a GitHub issue **with `--project telebuba`** so it lands in the board's `Backlog`. New deliverable work belongs on the board; the **column** (not a triage label) is authoritative for readiness — moving `Backlog → Ready` follows `.mex/context/kanban.md`, not the `ready-for-agent` label.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.
