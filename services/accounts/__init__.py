"""Business logic for the accounts domain.

Pure async functions: validate input, talk to ``core/*`` adapters, return
Pydantic models. No NiceGUI, no SQLAlchemy, no Telethon — those live in
``core/*``. UI handlers in ``features/accounts/`` are thin pass-throughs.

The implementations live in per-concern submodules:

- :mod:`.lifecycle` — registration + geo evaluation
- :mod:`.sessions`  — ``.session`` and tdata-archive imports + liveness check
- :mod:`.profile`   — profile-field updates (name / username / bio)
- :mod:`.privacy`   — Telegram privacy keys (who may see photo / bio / last seen)
- :mod:`.twofa`     — the account's Telegram cloud password + its recovery email
- :mod:`.media`     — profile photo / story / music uploads
- :mod:`.channels`  — own-channel management (create / edit / photo / delete)
- :mod:`.channel_posts` — own-channel posts (publish / list / edit / delete)

Proxies are a fleet-level pool, not an account sub-concern — that logic lives in
:mod:`services.proxies` over the ``proxies`` table.

This module is intentionally re-export only: per non-negotiable #11 callers in
``api/`` and tests take the public functions from here. Tests that need to
fake an external collaborator monkeypatch it on its owning submodule, e.g.
``services.accounts.sessions.convert_tdata_zip``.
"""

from __future__ import annotations

from core.db import list_accounts
from services.accounts._result import AccountActionError, AccountNotFoundError
from services.accounts._table import (
    InvalidCursorError,
    account_stats,
    list_accounts_page,
    list_listener_accounts,
)
from services.accounts._twofa_email import (
    cancel_account_twofa_email,
    clear_account_twofa_email,
    confirm_account_twofa_email,
    resend_account_twofa_email,
    set_account_twofa_email,
)
from services.accounts.channel_posts import (
    delete_account_channel_post,
    edit_account_channel_post,
    list_account_channel_posts,
    publish_account_channel_post,
)
from services.accounts.channels import (
    check_account_channel_username,
    create_account_channel,
    delete_account_channel,
    get_account_channel,
    list_account_channels,
    set_account_channel_photo,
    update_account_channel,
)
from services.accounts.lifecycle import (
    add_account,
    evaluate_account_geo,
    remove_account,
    require_account,
)
from services.accounts.login import (
    PhoneLoginError,
    logout_account,
    request_login_code,
    reset_account_session,
    start_phone_login,
    submit_login_code,
)
from services.accounts.media import (
    add_account_profile_music,
    post_account_story,
    remove_account_profile_music,
    remove_account_profile_photo,
    remove_account_story,
    resync_account_avatar,
    set_account_main_profile_photo,
    set_account_profile_photo,
    set_account_story_pinned,
)
from services.accounts.privacy import (
    apply_account_privacy,
    apply_privacy_to_all_accounts,
    read_account_privacy,
)
from services.accounts.profile import update_account_profile
from services.accounts.profile_read import (
    account_avatar_image,
    account_profile_image,
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
from services.accounts.twofa import (
    read_account_twofa,
    remove_account_twofa,
    set_account_twofa,
)
from services.accounts.web_login import (
    OpenWebResult,
    open_account_web,
    shutdown_web_login,
)

__all__ = [
    "AccountActionError",
    "AccountNotFoundError",
    "InvalidCursorError",
    "OpenWebResult",
    "PhoneLoginError",
    "SessionAlreadyExistsError",
    "account_avatar_image",
    "account_profile_image",
    "account_profile_view",
    "account_stats",
    "add_account",
    "add_account_profile_music",
    "apply_account_privacy",
    "apply_privacy_to_all_accounts",
    "cancel_account_twofa_email",
    "check_account_channel_username",
    "check_account_session",
    "clear_account_twofa_email",
    "confirm_account_twofa_email",
    "create_account_channel",
    "delete_account_channel",
    "delete_account_channel_post",
    "edit_account_channel_post",
    "evaluate_account_geo",
    "fetch_live_account_profile",
    "get_account_channel",
    "import_account_session",
    "import_account_tdata",
    "invalidate_account_profile_cache",
    "list_account_channel_posts",
    "list_account_channels",
    "list_accounts",
    "list_accounts_page",
    "list_listener_accounts",
    "logout_account",
    "open_account_web",
    "post_account_story",
    "publish_account_channel_post",
    "read_account_privacy",
    "read_account_twofa",
    "remove_account",
    "remove_account_profile_music",
    "remove_account_profile_photo",
    "remove_account_story",
    "remove_account_twofa",
    "request_login_code",
    "require_account",
    "resend_account_twofa_email",
    "reset_account_session",
    "resync_account_avatar",
    "set_account_channel_photo",
    "set_account_main_profile_photo",
    "set_account_profile_photo",
    "set_account_story_pinned",
    "set_account_twofa",
    "set_account_twofa_email",
    "shutdown_web_login",
    "start_phone_login",
    "submit_login_code",
    "update_account_channel",
    "update_account_profile",
]
