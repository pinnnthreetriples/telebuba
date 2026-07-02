"""Business logic for the accounts domain.

Pure async functions: validate input, talk to ``core/*`` adapters, return
Pydantic models. No NiceGUI, no SQLAlchemy, no Telethon — those live in
``core/*``. UI handlers in ``features/accounts/`` are thin pass-throughs.

The implementations live in per-concern submodules:

- :mod:`.lifecycle` — registration + geo evaluation
- :mod:`.sessions`  — ``.session`` and tdata-archive imports + liveness check
- :mod:`.profile`   — profile-field updates (name / username / bio)
- :mod:`.media`     — profile photo / story / music uploads

Proxies are a fleet-level pool, not an account sub-concern — that logic lives in
:mod:`services.proxies` over the ``proxies`` table.

This module is intentionally re-export only: per non-negotiable #11 callers in
``api/`` and tests take the public functions from here. Tests that need to
fake an external collaborator monkeypatch it on its owning submodule, e.g.
``services.accounts.sessions.convert_tdata_zip``.
"""

from __future__ import annotations

from core.db import list_accounts
from services.accounts._table import (
    InvalidCursorError,
    account_stats,
    list_accounts_page,
    list_listener_accounts,
    load_accounts_table,
)
from services.accounts.lifecycle import add_account, evaluate_account_geo, remove_account
from services.accounts.login import (
    PhoneLoginError,
    logout_account,
    request_login_code,
    reset_account_session,
    submit_login_code,
)
from services.accounts.media import (
    add_account_profile_music,
    post_account_story,
    remove_account_profile_music,
    remove_account_profile_photo,
    remove_account_story,
    set_account_profile_photo,
)
from services.accounts.profile import update_account_profile
from services.accounts.profile_read import (
    account_profile_view,
    fetch_live_account_profile,
    invalidate_account_profile_cache,
)
from services.accounts.sessions import (
    SessionAlreadyExistsError,
    check_account_session,
    import_account_session,
    import_account_tdata,
)

__all__ = [
    "InvalidCursorError",
    "PhoneLoginError",
    "SessionAlreadyExistsError",
    "account_profile_view",
    "account_stats",
    "add_account",
    "add_account_profile_music",
    "check_account_session",
    "evaluate_account_geo",
    "fetch_live_account_profile",
    "import_account_session",
    "import_account_tdata",
    "invalidate_account_profile_cache",
    "list_accounts",
    "list_accounts_page",
    "list_listener_accounts",
    "load_accounts_table",
    "logout_account",
    "post_account_story",
    "remove_account",
    "remove_account_profile_music",
    "remove_account_profile_photo",
    "remove_account_story",
    "request_login_code",
    "reset_account_session",
    "set_account_profile_photo",
    "submit_login_code",
    "update_account_profile",
]
