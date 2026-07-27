---
last_updated: 2026-07-27
---

# Add or Rename a Log Event
1. Name it `<domain>_<what_happened>` — `warming_`, `neurocomment_`, `account_`, `telegram_`, `proxy_`, `tdata_`. There is ONE `logs` table; the per-domain feeds separate only by `event LIKE 'prefix%'`, so an unprefixed name is invisible in its own feed and leaks into every other.
2. Prefix by the domain that *reaches* the code, not the layer that emits it: a `core/` helper with a single service consumer takes that consumer's prefix (`core/telegram_client/_listener.py` → `neurocomment_listener_*`) and leaves a `ponytail:` marker naming the second-consumer ceiling.
3. Never reuse one name across two domains. `retention_purge_failed` was shared and contaminated both feeds in both directions; it is now `warming_` / `neurocomment_retention_purge_failed`.
4. Add the label to `logEvent` in **both** `frontend/src/shared/i18n/en.json` and `ru.json`, same key order, distinct text per domain — identical text defeats the split on the global Logs page. `tests/test_logevent_i18n_parity.py` only catches literal-level, literal-name call sites, so dynamic names need a manual check.
5. Filters (`event_prefix`) take a comma-separated prefix list; empty means all rows, an all-blank value means none. Escaping lives in `_event_prefix_clause` (`core/repositories/logs.py`) — never build a `LIKE` from operator input by hand.
6. Test the rename at the layer the caller uses, and assert the event name, not just the level.

Verify: `rg -n '"<old_name>"'` across `.py`, `.ts(x)`, `.json`, `.md`, `.mex/`, `.github/` — comments and docstrings included. Editing an `api/v1` docstring changes the OpenAPI description, so re-run `uv run python -m tools.gen_api` or the CI drift gate fails. Views in effect: warming card `warming_,telegram_,spam_status` + `account_id` (`WarmingBoard.tsx`), neurocomment feed `neurocomment` (`NeurocommentPage.tsx`), Logs page unfiltered.
