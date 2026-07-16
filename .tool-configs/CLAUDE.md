@.mex/AGENTS.md

## Claude Code session rules

- Read `.mex/ROUTER.md` at session start; load only the routed context needed for the task.
- Before code changes run `npx mex-agent check --quiet`; use `npx mex-agent sync --dry-run` when drift is reported.
- Use Plan Mode for non-trivial or cross-layer changes.
- After meaningful work, update the compact state in `ROUTER.md`, refresh affected context/patterns, and use `mex log` for durable history.
- GitHub issues/PRDs: `docs/agents/issue-tracker.md`; labels: `docs/agents/triage-labels.md`; domain memory: `docs/agents/domain.md`.
