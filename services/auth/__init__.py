"""Auth service — policy over core.auth + the users repo. UI-agnostic."""

from __future__ import annotations

from services.auth._ratelimit import check_and_record as check_login_rate_limit
from services.auth.policy import (
    authenticate,
    issue_session_token,
    resolve_user,
    seed_admin_if_empty,
)

__all__ = [
    "authenticate",
    "check_login_rate_limit",
    "issue_session_token",
    "resolve_user",
    "seed_admin_if_empty",
]
