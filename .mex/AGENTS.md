---
name: agents
description: Canonical always-loaded project anchor.
last_updated: 2026-07-17
---

# Telebuba
Telegram operations dashboard for accounts, proxies, warming, neurocomment, profiles, and channels.

- Read `ROUTER.md`; load only its task route.
- Preserve `api → services → core` and typed Pydantic boundaries.
- External I/O goes through `core/`; never expose secrets, sessions, tdata, or proxy credentials.
- Add tests for behavior changes; test files ≤700 lines; run one uvicorn worker.
- Before code run `npx mex-agent check --quiet`; report only checks actually executed.
- Update `ROUTER.md` only when current reality changes; keep `mex log` entries to one sentence.
