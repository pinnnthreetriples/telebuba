"""Auth service — policy over core.auth + the users repo. UI-agnostic."""

from __future__ import annotations

from services.auth._argon2 import AuthenticationCapacityError
from services.auth._ratelimit import check_and_record as check_login_rate_limit
from services.auth._ratelimit import check_user_and_record as check_login_user_rate_limit
from services.auth._ratelimit import check_user_and_record as record_login_failure
from services.auth._ratelimit import user_blocked as login_user_blocked
from services.auth.policy import (
    authenticate,
    issue_session_token,
    resolve_and_refresh,
    resolve_user,
    revoke_sessions,
    seed_admin_if_empty,
)

__all__ = [
    "AuthenticationCapacityError",
    "authenticate",
    "check_login_rate_limit",
    "check_login_user_rate_limit",
    "issue_session_token",
    "login_user_blocked",
    "record_login_failure",
    "resolve_and_refresh",
    "resolve_user",
    "revoke_sessions",
    "seed_admin_if_empty",
]
