---
last_updated: 2026-08-06
---

# Setup and Checks

Requires Python 3.13, uv, Node 24/npm and Telegram API credentials.

```bash
uv sync --frozen
cp .env.example .env
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
cd frontend && npm ci && cd ..
uv run uvicorn main:app --reload
```

`.env.example` is the configuration reference. Login needs admin credentials and a 32+ byte `AUTH__SECRET`; provider keys are needed only for enabled features.

## Verify
```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check .
uv run pytest
uv run pre-commit run --all-files
uv run pre-commit run --hook-stage pre-push arch-guard --all-files
uv run pre-commit run --hook-stage pre-push aislop --all-files
uv run python -m tools.gen_api
npm audit --prefix frontend --package-lock-only --audit-level=info
npx --yes mex-agent@0.7.1 check && npx --yes mex-agent@0.7.1 doctor
cd frontend && npm run gates && npm run build
```

Run the relevant subset, not every command blindly. `.github/workflows/*.yml` are the CI source of truth. Code CI and MEX memory CI run on every PR/push to `main`. MEX blocks on every issue except `STALE_FILE`, which only nags: it counts commits and days across the WHOLE repo, so it reddens on other people's activity and blocking it would just train agents to bump `last_updated`. The weekly MEX schedule is a backstop when no PR is open.

Deep-domain memory is grounded: `grounds_to` in the frontmatter pins a claim to an exact code symbol, and CI rebuilds the graph (`.mex/graph.db`, ~94MB, gitignored) before checking. Delete or unrecoverably move a grounded symbol and `GROUNDING_GONE` fails the merge on the memory file that claimed it. Know the limit: a symbol RENAMED with its body intact is silently reconciled (by design), and a body edited IN PLACE reports nothing — `GROUNDING_DRIFT` needs a baseline in `_mex_grounded_source`, and mex-agent 0.7.1 never writes that table. So grounding catches deletions and refactors, NOT "the behaviour changed and the note did not". That case is still on whoever writes the PR.

The secret gate is CI's full-history scan, NOT the pre-commit hook: the hook scans a staged diff, which is 0 commits after a CI checkout, so it would pass unconditionally — CI skips it and runs history detection instead, which needs full fetch depth. Never add `.gitleaks.toml` or `.gitleaksignore` to this repo: `detect` auto-loads both, and either one turns the gate green by its own mechanism (a repo-local config REPLACES the ruleset; the ignore file suppresses by fingerprint). CI asserts a non-zero commits-scanned count for the same reason — a container that scans nothing exits 0. Implementation (image digest, flags) stays in `ci.yml`. Never plant a test secret here to exercise it; use a throwaway repo.

On Windows checkouts with `core.autocrlf=true`, repo-wide format hooks may rewrite pre-existing CRLF files. Format touched files and verify real scope with `git diff HEAD --name-only` rather than relying on `git status` alone.
