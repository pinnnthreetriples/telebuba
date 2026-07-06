@.mex/AGENTS.md

## Claude Code — session rules

- Before writing code: `npx mex-agent check --quiet`; on drift errors, `npx mex-agent sync --dry-run` first.
- Plan Mode (`/plan`) for non-trivial tasks (new features, cross-layer changes); trivial fixes go straight to execution.
- After meaningful work, run the GROW step: update `state/active.md`, bump `last_updated`, move board item.

## Agent skills

### Issue tracker

Issues and PRDs live as **GitHub issues** in the telebuba repo (owner `pinnnthreetriples`), managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical triage roles map 1:1 to GitHub labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Domain knowledge lives in the **`.mex/` scaffold** — entrypoint `.mex/ROUTER.md`, details in `.mex/context/`. See `docs/agents/domain.md`.
