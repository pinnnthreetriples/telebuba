---
last_updated: 2026-07-16
---

# MEX Sync

```bash
npx mex-agent check --json
npx mex-agent sync --dry-run
npx mex-agent sync
npx mex-agent check
npx mex-agent doctor
```

## Sync rules

- Compare claims with current code and workflows; never update dates without verifying content.
- Keep `.mex/AGENTS.md` under roughly 200 tokens and `ROUTER.md` as a short live snapshot.
- Load and edit only affected `context/` and `patterns/` files.
- Preserve decision history. Mark superseded ADRs instead of deleting them.
- Put implementation history in `.mex/events/decisions.jsonl` through `mex log`, not in `ROUTER.md` or `state/active.md`.
- Bump `last_updated` only on files actually reviewed.
- Finish with `mex check` and report any remaining warnings honestly.
