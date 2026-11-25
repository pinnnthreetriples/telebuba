"""An open credential folder reaches the operator's bot, once for the whole fleet."""

from __future__ import annotations

from schemas.logs import LogEntry
from services._notification_messages import FLEET_KINDS, compose_message, notification_kind


def _entry(event: str, **extra: object) -> LogEntry:
    return LogEntry(
        id=1,
        created_at="now",
        level="WARNING",
        status="warning",
        account_id=None,
        event=event,
        extra=dict(extra),
    )


def test_the_open_folder_event_is_a_fleet_notification_that_names_the_command() -> None:
    entry = _entry("private_path_open", what="vault_mirror", grants=["authenticated_users"])

    assert notification_kind(entry) == "private_path_open"
    assert "private_path_open" in FLEET_KINDS
    assert "python -m tools.secure_paths fix" in compose_message("private_path_open", "")


def test_a_failed_check_is_not_a_notification() -> None:
    assert notification_kind(_entry("private_path_check_failed", error_type="OSError")) is None
