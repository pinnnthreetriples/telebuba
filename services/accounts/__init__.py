"""Business logic for the accounts domain.

Pure async functions: validate input, talk to ``core/*`` adapters, return
Pydantic models. No NiceGUI, no SQLAlchemy, no Telethon — those live in
``core/*``. UI handlers in ``features/accounts/`` are thin pass-throughs.

The implementations live in per-concern submodules:

- :mod:`.lifecycle` — registration + geo evaluation
- :mod:`.sessions`  — ``.session`` and tdata-archive imports + liveness check
- :mod:`.proxy`     — proxy attach / detach / connectivity probe
- :mod:`.profile`   — profile-field updates (name / username / bio)
- :mod:`.media`     — profile photo / story / music uploads

This module is intentionally re-export only: per non-negotiable #11 callers in
``features/`` and tests take the public functions from here. Tests that need to
fake an external collaborator monkeypatch it on its owning submodule, e.g.
``services.accounts.sessions.convert_tdata_zip`` or
``services.accounts.proxy.check_proxy_connectivity``.
"""

from __future__ import annotations

from core.db import list_accounts
from services.accounts._table import load_accounts_table
from services.accounts.lifecycle import add_account, evaluate_account_geo, remove_account
from services.accounts.media import (
    add_account_profile_music,
    post_account_story,
    set_account_profile_photo,
)
from services.accounts.profile import update_account_profile
from services.accounts.profile_read import (
    fetch_live_account_profile,
    invalidate_account_profile_cache,
)
from services.accounts.proxy import (
    check_account_proxy,
    delete_account_proxy,
    fetch_account_proxy_settings,
    save_account_proxy,
)
from services.accounts.sessions import (
    SessionAlreadyExistsError,
    check_account_session,
    import_account_session,
    import_account_tdata,
)

__all__ = [
    "SessionAlreadyExistsError",
    "add_account",
    "add_account_profile_music",
    "check_account_proxy",
    "check_account_session",
    "delete_account_proxy",
    "evaluate_account_geo",
    "fetch_account_proxy_settings",
    "fetch_live_account_profile",
    "import_account_session",
    "import_account_tdata",
    "invalidate_account_profile_cache",
    "list_accounts",
    "load_accounts_table",
    "post_account_story",
    "remove_account",
    "save_account_proxy",
    "set_account_profile_photo",
    "update_account_profile",
]
