@.mex/AGENTS.md

## Claude Code — session rules

- At the start of every session run `npx mex-agent check --quiet` and report the drift score.
- If drift errors are found, run `npx mex-agent sync --dry-run` before writing any code.
- Always start tasks in Plan Mode (`/plan`) before switching to execution.
- After meaningful work, run the GROW step: update `state/active.md`, bump `last_updated`, move board item.

## Agent skills

### Issue tracker

Issues and PRDs live as **GitHub issues** in the telebuba repo (owner `pinnnthreetriples`), managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical triage roles map 1:1 to GitHub labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Domain knowledge lives in the **`.mex/` scaffold** — entrypoint `.mex/ROUTER.md`, details in `.mex/context/`. See `docs/agents/domain.md`.
