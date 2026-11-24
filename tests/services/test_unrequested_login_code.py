"""Login codes nobody asked for: journalled without the code; ours and other messages are not."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.telegram_client._message_listener import _service_kind
from schemas.inbox_events import InboxMessageReceived
from services import inbox_events
from services.accounts import _expected_codes

_CODE_TEXT = "Login code: 48213. Do not give this code to anyone"


@pytest.fixture(autouse=True)
def _fresh() -> None:
    _expected_codes.reset_for_tests()


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    seen: list[tuple[str, str, str]] = []

    async def _log(level: str, event: str, **kwargs: object) -> None:
        seen.append((level, event, str(kwargs)))

    monkeypatch.setattr(inbox_events, "log_event", _log)
    monkeypatch.setattr(inbox_events, "publish", lambda _event: None)
    return seen


def _event(kind: str | None) -> InboxMessageReceived:
    return InboxMessageReceived(
        account_id="acc",
        peer_type="user",
        peer_id="777000",
        message_id=5,
        service_kind=kind,  # ty: ignore[invalid-argument-type]
    )


def test_the_gateway_classifies_only_the_service_user_and_keeps_the_code_out() -> None:
    code = SimpleNamespace(message=_CODE_TEXT)
    other = SimpleNamespace(message="New login from Chrome on Windows")
    assert _service_kind("user", 777000, code) == "login_code"
    assert _service_kind("user", 777000, other) == "other"
    assert _service_kind("user", 12345, code) is None
    assert _service_kind("chat", 777000, code) is None
    assert "48213" not in repr(_event("login_code"))


@pytest.mark.asyncio
async def test_a_code_nobody_asked_for_is_a_warning_without_the_code(
    events: list[tuple[str, str, str]],
) -> None:
    await inbox_events.on_message_received(_event("login_code"))

    assert [(level, name) for level, name, _ in events] == [("WARNING", "login_code_unrequested")]
    assert "48213" not in repr(events)


@pytest.mark.asyncio
async def test_an_expected_code_makes_no_event(events: list[tuple[str, str, str]]) -> None:
    _expected_codes.expect_login_code("acc", 60)

    await inbox_events.on_message_received(_event("login_code"))

    assert events == []


@pytest.mark.asyncio
async def test_a_running_sign_in_counts_as_expecting(
    events: list[tuple[str, str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = asyncio.Lock()
    monkeypatch.setattr(inbox_events, "account_auth_lock", lambda _id: lock)
    async with lock:
        await inbox_events.on_message_received(_event("login_code"))

    assert events == []


@pytest.mark.asyncio
async def test_an_expired_window_no_longer_covers_a_code(
    events: list[tuple[str, str, str]],
) -> None:
    _expected_codes.expect_login_code("acc", 0.0)

    await inbox_events.on_message_received(_event("login_code"))

    assert [name for _level, name, _extra in events] == ["login_code_unrequested"]


@pytest.mark.asyncio
async def test_any_other_service_message_is_info_and_other_peers_are_silent(
    events: list[tuple[str, str, str]],
) -> None:
    await inbox_events.on_message_received(_event("other"))
    await inbox_events.on_message_received(_event(None))

    assert [(level, name) for level, name, _ in events] == [("INFO", "telegram_service_message")]


@pytest.mark.asyncio
async def test_a_failing_journal_never_breaks_inbox_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[object] = []

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError

    monkeypatch.setattr(inbox_events, "publish", published.append)
    monkeypatch.setattr(inbox_events, "log_event", _boom)

    await inbox_events.on_message_received(_event("login_code"))

    assert len(published) == 1


def test_claiming_a_code_request_opens_the_window() -> None:
    from services.accounts import devices  # noqa: PLC0415

    devices._LAST_CODE_REQUEST.pop("acc", None)
    devices.claim_code_request("acc")
    devices._LAST_CODE_REQUEST.pop("acc", None)

    assert _expected_codes.is_login_code_expected("acc")
