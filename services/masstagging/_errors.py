"""Refusals of the masstagging domain: stable codes and the classes that carry them.

``api/`` maps the class to the HTTP status; the SPA words the code (``shell.code.*``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas.masstagging import MasstaggingRefusalCode

CAMPAIGN_RUNNING: MasstaggingRefusalCode = "campaign_running"
CAMPAIGN_CHANGED: MasstaggingRefusalCode = "campaign_changed"
ACCOUNT_NOT_FOUND: MasstaggingRefusalCode = "account_not_found"
TOO_MANY_TARGETS: MasstaggingRefusalCode = "too_many_targets"
NO_ACCOUNTS: MasstaggingRefusalCode = "masstagging_no_accounts"
NO_FREE_ACCOUNTS: MasstaggingRefusalCode = "masstagging_no_free_accounts"
NO_TARGETS: MasstaggingRefusalCode = "masstagging_no_targets"
NO_MEDIA: MasstaggingRefusalCode = "masstagging_no_media"
INVALID_TARGET: MasstaggingRefusalCode = "masstagging_invalid_target"
TOO_MANY_ACCOUNTS: MasstaggingRefusalCode = "masstagging_too_many_accounts"
TOO_MANY_MEDIA: MasstaggingRefusalCode = "masstagging_too_many_media"
TARGET_NOT_FOUND: MasstaggingRefusalCode = "masstagging_target_not_found"
TARGET_STATE_CHANGED: MasstaggingRefusalCode = "masstagging_target_state_changed"
MEDIA_INVALID: MasstaggingRefusalCode = "masstagging_media_invalid"
MEDIA_TOO_LARGE: MasstaggingRefusalCode = "masstagging_media_too_large"
IMPORT_INVALID: MasstaggingRefusalCode = "masstagging_import_invalid"
BASE_NOT_FOUND: MasstaggingRefusalCode = "masstagging_base_not_found"


class MasstaggingRefusedError(Exception):
    """A refusal with a stable code; the subclass picks the HTTP status."""

    def __init__(self, code: MasstaggingRefusalCode) -> None:
        self.code = code
        super().__init__(code)


class MasstaggingConflictError(MasstaggingRefusedError):
    """409: the campaign's state does not allow this right now."""


class MasstaggingInvalidError(MasstaggingRefusedError):
    """400: the request itself cannot be accepted."""
