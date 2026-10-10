---
name: setup
description: Setup, commands, CI, hooks, Windows checkout and verification.
last_updated: 2026-10-10
edges:
  - target: context/architecture.md
    condition: layer boundaries, gateways or system design
  - target: patterns/INDEX.md
    condition: the change is a repeatable implementation task
---

# Setup and Checks

Requires Python 3.13.14, uv, Node 24/npm and Telegram API credentials.

```bash
uv sync --frozen
cp .env.example .env
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
cd frontend && npm ci && cd ..
uv run uvicorn main:app
```

No `--reload`/`--workers`: both force a Windows SelectorEventLoop that cannot spawn the web-login browser. `.env.example` is the config reference; login needs admin creds and a 32+ byte `AUTH__SECRET`, provider keys only for enabled features.

## Verify
```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check .
uv run pytest
uv run pre-commit run --all-files
uv run pre-commit run --hook-stage pre-push arch-guard --all-files
uv run pre-commit run --hook-stage pre-push aislop --all-files
uv run python -m tools.gen_api
uv run pip-audit --strict && uv run semgrep --config auto --error .
node frontend/scripts/npm-audit-gate.mjs  # npm audit + reviewed ignores
npx --yes mex-agent@0.8.3 check && npx --yes mex-agent@0.8.3 doctor
cd frontend && npm run gates && npm run build
```

Run the relevant subset. `.github/workflows/*.yml` are the CI source of truth. Code CI and MEX memory CI run on every PR/push to `main`. MEX blocks on every issue except `STALE_FILE`, which only nags: it counts commits and days across the WHOLE repo, so it reddens on other people's activity and blocking it would just train agents to bump `last_updated`. The weekly MEX schedule is a backstop when no PR is open. A Claude Code Stop hook (`tools/mex_grow_gate.py`) demands GROW once per session and HEAD when branch commits touch code but not `.mex/`. Nightly is the third workflow and gates no PR: its mutation sweep over `services/`/`schemas/` is far too slow for one, so it holds a tracked aggregate score floor — never a demand that each mutant die — and reports catalogue drift rather than failing on it. Policy lives in `docs/mutation-testing.md`.

Deep-domain memory is grounded: `grounds_to` in the frontmatter pins a claim to an exact code symbol, and CI rebuilds the graph (`.mex/graph.db`, gitignored, never committed) before checking. Delete a grounded symbol → `GROUNDING_GONE` (error). Change its BODY under a note still describing the old behaviour → `GROUNDING_DRIFT` (warning, and warnings block). A rename that keeps the body is silently reconciled as a move, by design.

Drift needs a baseline and a rebuilt graph has none, so each `grounds_to` entry carries `bodyHash`, which mex compares to the node's current body. An entry without it is never drift-checked, so CI fails it. **If you change a grounded function, re-read the note against the new code, fix its prose, then re-pin `bodyHash` with the tool below.** The hash lives in the note on purpose: re-pinning it edits that note, so it lands in the diff where a reviewer judges whether it still holds. CI rebuilds the graph on each run while cached graphs are unreliable; this takes minutes but keeps the grounding gate active.

To ground a NEW claim: find the symbol with `mex graph query where-defined` or `mex graph scope`, read it with `mex graph get <id> --detail source`, then `python3 tools/mex_grounds_to.py <node-id>` prints the whole entry, `bodyHash` included (`mex graph get` does not expose it). Prefer it over `mex graph scope --fingerprint`, which attaches a 1.7–16kB blob to every node it returns. Ground only functions that embody a claim the prose already makes, and keep broad convention files (`architecture`, `conventions`, `frontend`, this file) ungrounded — that split is mex's own rule, not ours. Where prose already names a load-bearing symbol, wrapping that visible name in a `mex://` anchor makes it navigable and puts the id under the same check; the anchor carries the node id alone, never a fingerprint.

When a check does fail, `npx --yes mex-agent@0.8.3 sync --dry-run --warnings` prints a targeted repair prompt per flagged file instead of leaving you to guess what drifted.

The secret gate is CI's full-history scan, NOT the pre-commit hook: the hook scans a staged diff, which is 0 commits after a CI checkout, so it would pass unconditionally — CI skips it and runs history detection instead, which needs full fetch depth. Keep `.gitleaks.toml` and `.gitleaksignore` absent from this repo: `detect` auto-loads both, and either one turns the gate green by its own mechanism (a repo-local config REPLACES the ruleset; the ignore file suppresses by fingerprint). CI asserts a non-zero commits-scanned count for the same reason — a container that scans nothing exits 0. Implementation (image digest, flags) stays in `ci.yml`. Never plant a test secret here to exercise it; use a throwaway repo.

On Windows checkouts with `core.autocrlf=true`, repo-wide format hooks may rewrite pre-existing CRLF files. Format touched files and verify real scope with `git diff HEAD --name-only` rather than relying on `git status` alone.
