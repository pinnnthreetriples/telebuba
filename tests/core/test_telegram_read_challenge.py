"""Tests for the ``WaitForBotChallenge`` match predicate (Ф2 #145).

The ``events.NewMessage`` subscription + ``asyncio.wait_for`` shell is pure I/O
(``pragma: no cover``); the branchy match logic lives in ``_extract_bot_challenge``
and is unit-tested here against duck-typed message objects. Predicate bias:
false-negative > false-positive (a wrong click can get the account kicked).
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from telethon.tl.types import (
    KeyboardButtonCallback,
    KeyboardButtonRow,
    MessageActionChatAddUser,
    MessageEntityMentionName,
    MessageEntityTextUrl,
    MessageMediaPhoto,
    ReplyInlineMarkup,
)

from core.telegram_client._read_challenge import (
    _extract_bot_challenge,
    download_challenge_image,
)
from schemas.challenge import BotChallengeMessage

_MY_ID = 12345
_MY_USERNAME = "marina_bot_solver"


def _markup() -> ReplyInlineMarkup:
    return ReplyInlineMarkup(
        rows=[
            KeyboardButtonRow(buttons=[KeyboardButtonCallback(text="Я не бот", data=b"ok")]),
            KeyboardButtonRow(buttons=[KeyboardButtonCallback(text="Я бот", data=b"no")]),
        ],
    )


_DEFAULT_MARKUP = object()


def _msg(
    *,
    bot: bool = True,
    reply_markup: object = _DEFAULT_MARKUP,
    text: str = "",
    entities: list[object] | None = None,
    media: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        sender=SimpleNamespace(bot=bot),
        reply_markup=_markup() if reply_markup is _DEFAULT_MARKUP else reply_markup,
        message=text,
        entities=entities or [],
        media=media,
        id=555,
    )


def _extract(message: object, replied_action: object | None = None) -> BotChallengeMessage | None:
    return _extract_bot_challenge(
        message,
        replied_action=replied_action,
        my_user_id=_MY_ID,
        my_username=_MY_USERNAME,
    )


def test_match_by_username_mention() -> None:
    result = _extract(_msg(text=f"@{_MY_USERNAME} нажми кнопку, чтобы остаться"))
    assert result == BotChallengeMessage(
        text=f"@{_MY_USERNAME} нажми кнопку, чтобы остаться",
        button_labels=["Я не бот", "Я бот"],
        message_id=555,
        has_photo=False,
    )


def test_match_by_tg_user_id_text_url_entity() -> None:
    entity = MessageEntityTextUrl(offset=0, length=4, url=f"tg://user?id={_MY_ID}")
    result = _extract(_msg(text="нажми кнопку", entities=[entity]))
    assert result is not None
    assert result.button_labels == ["Я не бот", "Я бот"]


def test_match_by_mention_name_entity() -> None:
    entity = MessageEntityMentionName(offset=0, length=4, user_id=_MY_ID)
    result = _extract(_msg(text="нажми кнопку", entities=[entity]))
    assert result is not None


def test_match_by_reply_to_join_service_message() -> None:
    action = MessageActionChatAddUser(users=[_MY_ID])
    result = _extract(_msg(text="новый участник, докажи что не бот"), replied_action=action)
    assert result is not None


def test_no_match_when_sender_not_a_bot() -> None:
    assert _extract(_msg(bot=False, text=f"@{_MY_USERNAME}")) is None


def test_no_match_without_inline_markup() -> None:
    assert _extract(_msg(reply_markup=None, text=f"@{_MY_USERNAME}")) is None


def test_no_match_when_addressed_to_another_user() -> None:
    # Parallel join wave: mention/entity/reply all point at someone else.
    other_entity = MessageEntityMentionName(offset=0, length=4, user_id=999)
    other_action = MessageActionChatAddUser(users=[999])
    msg = _msg(text="@somebody_else докажи что не бот", entities=[other_entity])
    assert _extract(msg, replied_action=other_action) is None


def test_no_match_when_no_addressing_signal() -> None:
    # Bot + inline markup but a generic broadcast with no mention/entity/reply.
    assert _extract(_msg(text="нажмите кнопку ниже"), replied_action=None) is None


def test_has_photo_flag_set_for_image_challenge() -> None:
    photo = MagicMock(spec=MessageMediaPhoto)
    result = _extract(_msg(text=f"@{_MY_USERNAME}", media=photo))
    assert result is not None
    assert result.has_photo is True


class _FakeDownloadClient:
    """Stub Telethon client whose ``download_media`` returns preset bytes (or None)."""

    def __init__(self, data: object) -> None:
        self._data = data

    async def download_media(self, _message: object, *, file: object) -> object:
        assert file is bytes  # in-memory download, not a path
        return self._data


@pytest.mark.asyncio
async def test_download_challenge_image_returns_base64() -> None:
    client = _FakeDownloadClient(b"\x89PNG-bytes")
    result = await download_challenge_image(client, object())  # ty: ignore[invalid-argument-type]
    assert result == base64.b64encode(b"\x89PNG-bytes").decode("ascii")


@pytest.mark.asyncio
async def test_download_challenge_image_none_when_no_bytes() -> None:
    # A media message that yields no bytes (e.g. download failed) → None, not a crash.
    client = _FakeDownloadClient(None)
    assert await download_challenge_image(client, object()) is None  # ty: ignore[invalid-argument-type]
