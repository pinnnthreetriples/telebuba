"""Message and profile-field tests for the typed-action dispatcher."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.account import (
    UpdateProfileRequest,
    UpdateUsernameRequest,
)

from core.config import settings
from core.db import create_account, fetch_account
from core.telegram_client import execute
from core.telegram_client._actions import _typing_seconds
from schemas.accounts import AccountCreate
from schemas.telegram_actions import (
    ClickButton,
    CommentOnPost,
    PostComment,
    SendDirectMessage,
    UpdateProfile,
)
from tests.core.telegram_client.helpers import patch_action_client as _patch_client


@pytest.mark.asyncio
async def test_execute_post_comment_returns_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_message = MagicMock(id=4242)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def send_message(self, chat_id: int, text: str) -> object:
            assert chat_id == 12345
            assert text == "hi"
            return sent_message

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-3", PostComment(chat_id=12345, text="hi"))

    assert result.status == "ok"
    assert result.message_id == 4242
    assert result.action_type == "post_comment"


@pytest.mark.asyncio
async def test_execute_comment_on_post_returns_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_message = MagicMock(id=8181)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def send_message(self, entity: str, text: str, *, comment_to: int) -> object:
            assert entity == "@news"
            assert text == "great post"
            assert comment_to == 55
            return sent_message

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-comment",
        CommentOnPost(channel="@news", post_id=55, text="great post"),
    )

    assert result.status == "ok"
    assert result.action_type == "comment_on_post"
    assert result.message_id == 8181


@pytest.mark.asyncio
async def test_execute_comment_on_post_handles_flood_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def send_message(self, _entity: str, _text: str, *, comment_to: int) -> object:  # noqa: ARG002
            raise errors.FloodWaitError(request=None, capture=17)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-comment-flood",
        CommentOnPost(channel="@news", post_id=55, text="hi"),
    )

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 17


@pytest.mark.asyncio
async def test_execute_comment_on_post_write_forbidden_surfaces_error_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A domain error must surface its exception class name for #117 to branch on."""

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def send_message(self, _entity: str, _text: str, *, comment_to: int) -> object:  # noqa: ARG002
            raise errors.ChatWriteForbiddenError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-comment-forbidden",
        CommentOnPost(channel="@news", post_id=55, text="hi"),
    )

    assert result.status == "failed"
    assert result.error_type == "ChatWriteForbiddenError"


@pytest.mark.asyncio
async def test_execute_click_button_clicks_by_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clicked: list[object] = []
    message = MagicMock()

    async def fake_click(i: object = None, *, text: object = None) -> object:
        clicked.append((i, text))
        return MagicMock()

    message.click = fake_click

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, chat_id: int, *, ids: int) -> object:
            assert chat_id == 123
            assert ids == 456
            return message

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-click",
        ClickButton(chat_id=123, message_id=456, button_index=2),
    )

    assert result.status == "ok"
    assert result.action_type == "click_button"
    assert clicked == [(2, None)]


@pytest.mark.asyncio
async def test_execute_click_button_clicks_by_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clicked: list[object] = []
    message = MagicMock()

    async def fake_click(i: object = None, *, text: object = None) -> object:
        clicked.append((i, text))
        return MagicMock()

    message.click = fake_click

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, _chat_id: int, *, ids: int) -> object:  # noqa: ARG002
            return message

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-click-text",
        ClickButton(chat_id=123, message_id=456, button_text="I am not a robot"),
    )

    assert result.status == "ok"
    assert clicked == [(None, "I am not a robot")]


@pytest.mark.asyncio
async def test_execute_click_button_defaults_to_first_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clicked: list[object] = []
    message = MagicMock()

    async def fake_click(i: object = None, *, text: object = None) -> object:
        clicked.append((i, text))
        return MagicMock()

    message.click = fake_click

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, _chat_id: int, *, ids: int) -> object:  # noqa: ARG002
            return message

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-click-default", ClickButton(chat_id=1, message_id=2))

    assert result.status == "ok"
    assert clicked == [(0, None)]


@pytest.mark.asyncio
async def test_execute_click_button_no_message_is_noop_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the message is gone there is nothing to click — succeed as a no-op."""

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, _chat_id: int, *, ids: int) -> object:  # noqa: ARG002
            return None

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-click-missing", ClickButton(chat_id=1, message_id=2))

    assert result.status == "ok"
    assert result.message_id is None


@pytest.mark.asyncio
async def test_execute_update_profile_dispatches_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-4",
        UpdateProfile(first_name="Alice", last_name="L", username="alice", bio="Bio"),
    )

    assert result.status == "ok"
    assert any(isinstance(req, UpdateProfileRequest) for req in captured)
    assert any(isinstance(req, UpdateUsernameRequest) for req in captured)


@pytest.mark.asyncio
async def test_execute_update_profile_none_fields_are_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``None`` must reach the TL request as ``None`` (omitted = unchanged).

    Regression guard for the old ``last_name or ""`` coercion, which turned
    "leave my last name alone" into "clear my last name" — and for the
    username: a ``None`` username must not dispatch ``UpdateUsernameRequest``.
    """
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-4-none", UpdateProfile(first_name="Alice"))

    assert result.status == "ok"
    profile_req = next(req for req in captured if isinstance(req, UpdateProfileRequest))
    assert profile_req.first_name == "Alice"
    assert profile_req.last_name is None
    assert profile_req.about is None
    assert not any(isinstance(req, UpdateUsernameRequest) for req in captured)


@pytest.mark.asyncio
async def test_execute_update_profile_empty_strings_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``""`` must reach the TL requests verbatim — the "clear this field" form.

    ``account.updateProfile`` serializes ``""`` (flag set → server clears) and
    ``UpdateUsernameRequest(username="")`` removes the username.
    """
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-4-clear",
        UpdateProfile(first_name="Alice", last_name="", username="", bio=""),
    )

    assert result.status == "ok"
    profile_req = next(req for req in captured if isinstance(req, UpdateProfileRequest))
    assert profile_req.last_name == ""
    assert profile_req.about == ""
    username_req = next(req for req in captured if isinstance(req, UpdateUsernameRequest))
    assert username_req.username == ""


@pytest.mark.asyncio
async def test_execute_update_profile_sends_username_before_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallible username update must run before profile fields change."""
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-4-order",
        UpdateProfile(first_name="Alice", username="alice", bio="Bio"),
    )

    assert result.status == "ok"
    assert isinstance(captured[0], UpdateUsernameRequest)
    assert isinstance(captured[1], UpdateProfileRequest)


@pytest.mark.asyncio
async def test_execute_update_profile_occupied_username_leaves_profile_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused username fails before any profile field changes."""
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)
            if isinstance(request, UpdateUsernameRequest):
                raise errors.UsernameOccupiedError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-4-occupied",
        UpdateProfile(first_name="Alice", username="taken", bio="Bio"),
    )

    assert result.status != "ok"
    # Stable locale-neutral code, not Telethon's English prose (the SPA translates).
    assert result.error_message == "username_occupied"
    assert not any(isinstance(req, UpdateProfileRequest) for req in captured)


@pytest.mark.asyncio
async def test_execute_update_profile_username_not_modified_is_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-sending the current username is a successful no-op."""
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)
            if isinstance(request, UpdateUsernameRequest):
                raise errors.UsernameNotModifiedError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-4-same-username",
        UpdateProfile(first_name="Alice", username="alice", bio="Bio"),
    )

    assert result.status == "ok"
    assert any(isinstance(req, UpdateProfileRequest) for req in captured)


@pytest.mark.asyncio
async def test_execute_update_profile_invalid_username_yields_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            if isinstance(request, UpdateUsernameRequest):
                raise errors.UsernameInvalidError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-4-invalid",
        UpdateProfile(first_name="Alice", username="sobad"),
    )

    assert result.status == "failed"
    assert result.error_message == "username_invalid"


@pytest.mark.asyncio
async def test_execute_frozen_account_yields_stable_code_and_marks_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frozen refusal returns ``account_frozen`` AND flips the stored status.

    Previously the Frozen* family fell into the generic ladder: raw English
    prose on the wire and an accounts list that stayed "alive" until the next
    manual session check.
    """
    await create_account(AccountCreate(account_id="acc-frozen"))

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.FrozenMethodInvalidError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-frozen", UpdateProfile(first_name="Alice"))

    assert result.status == "failed"
    assert result.error_message == "account_frozen"
    account = await fetch_account("acc-frozen")
    assert account is not None
    assert account.status == "frozen"
    assert account.last_checked_at is not None


@pytest.mark.asyncio
async def test_execute_flood_wait_marks_db_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flood-limited action stores ``flood_wait`` so the list/tiles reflect it."""
    await create_account(AccountCreate(account_id="acc-flooded"))

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.FloodWaitError(request=None, capture=30)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-flooded", UpdateProfile(first_name="Alice"))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 30
    account = await fetch_account("acc-flooded")
    assert account is not None
    assert account.status == "flood_wait"


def test_typing_seconds_scales_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "typing_wpm", 45)
    monkeypatch.setattr(settings.warming, "typing_sim_min_seconds", 0.5)
    monkeypatch.setattr(settings.warming, "typing_sim_max_seconds", 12.0)
    assert _typing_seconds("") == 0.5  # clamp to min
    assert _typing_seconds("x" * 20) == pytest.approx(20 * 60 / (5 * 45))
    assert _typing_seconds("x" * 1000) == 12.0  # clamp to max


@pytest.mark.asyncio
async def test_execute_send_dm_simulates_typing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "typing_simulation_enabled", True)
    monkeypatch.setattr(settings.warming, "typing_sim_min_seconds", 0.0)
    monkeypatch.setattr(settings.warming, "typing_sim_max_seconds", 0.0)
    typed = {"flag": False}

    class FakeClient:
        async def connect(self) -> None:
            return None

        def action(self, _entity: object, _action: str) -> object:
            @asynccontextmanager
            async def cm():
                typed["flag"] = True
                yield

            return cm()

        async def send_message(self, user_id: int, text: str) -> object:
            assert user_id == 42
            assert text == "привет"
            return MagicMock(id=555)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc", SendDirectMessage(user_id=42, text="привет"))

    assert result.status == "ok"
    assert result.message_id == 555
    assert typed["flag"] is True


@pytest.mark.asyncio
async def test_execute_send_dm_without_typing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "typing_simulation_enabled", False)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def send_message(self, _user_id: int, _text: str) -> object:
            return MagicMock(id=7)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc", SendDirectMessage(user_id=42, text="hi"))

    assert result.status == "ok"
    assert result.message_id == 7
