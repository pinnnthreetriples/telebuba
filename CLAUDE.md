# Telebuba
Read `.mex/ROUTER.md`; load only its task route.

Rules: `api → services → core`, Pydantic boundaries, external I/O through `core/`, tests for behavior changes, one uvicorn worker, no secret/session exposure.

Before code run `npx mex-agent check --quiet`; report only checks actually run. Update `ROUTER.md` only when current state changes; keep each `mex log` message to one sentence.
