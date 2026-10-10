"""Gateway tests for masstagging: the auto-delete toggle, target lookups, story mentions."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from PIL import Image
from telethon import errors
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.functions.messages import SetHistoryTTLRequest
from telethon.tl.functions.stories import SendStoryRequest
from telethon.tl.types import (
    Channel,
    Chat,
    ChatPhotoEmpty,
    InputMessageEntityMentionName,
    InputPeerUser,
    MediaAreaUrl,
    User,
)

from core.telegram_client import execute, execute_read
from core.telegram_client._story_link import caption_with_mentions, draw_link_plate
from schemas.telegram_actions import PostStory
from schemas.telegram_actions_masstagging import (
    ListMasstagContacts,
    MasstagContactsResult,
    MasstagResolvedTarget,
    MasstagToggleTtl,
    ResolveMasstagTarget,
)
from schemas.telegram_actions_media import StoryLink, StoryMention
from tests.core.telegram_client.helpers import patch_action_client, patch_read_client


def _user(user_id: int, username: str | None = None, **flags: Any) -> User:
    return User(id=user_id, username=username, first_name=f"n{user_id}", **flags)


def _jpeg() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (400, 600), (10, 20, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


class _Client:
    """Records every request; answers ``get_entity`` from a table."""

    def __init__(self, entities: dict[object, object] | None = None) -> None:
        self.requests: list[object] = []
        self.entities = entities or {}
        self.participants: list[object] | Exception = []

    async def connect(self) -> None:
        return None

    async def get_input_entity(self, peer: object) -> object:
        if peer == "me":
            return MagicMock()
        return InputPeerUser(user_id=int(str(peer)), access_hash=7)

    async def get_entity(self, target: object) -> object:
        found = self.entities.get(target)
        if isinstance(found, Exception):
            raise found
        if found is None:
            msg = f'Cannot find any entity corresponding to "{target}"'
            raise ValueError(msg)
        return found

    async def get_participants(self, _entity: object, *, limit: int) -> list[object]:
        if isinstance(self.participants, Exception):
            raise self.participants
        return self.participants[:limit]

    async def upload_file(self, file: BytesIO, *, file_name: str) -> object:
        _ = file_name
        self.uploaded = file.read()
        return MagicMock()

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        if isinstance(request, SendStoryRequest):
            return SimpleNamespace(updates=[SimpleNamespace(story=SimpleNamespace(id=55))])
        if isinstance(request, GetContactsRequest):
            return SimpleNamespace(users=[_user(1, "alpha"), _user(2), _user(3, bot=True)])
        return MagicMock()


@pytest.mark.asyncio
async def test_the_toggle_turns_auto_delete_on_and_off_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client()
    patch_action_client(monkeypatch, client)

    result = await execute("acc", MasstagToggleTtl(peer="42", hold_seconds=0))

    assert result.status == "ok"
    periods = [r.period for r in client.requests if isinstance(r, SetHistoryTTLRequest)]
    assert periods == [86_400, 0]


@pytest.mark.asyncio
async def test_a_privacy_refusal_reaches_the_caller_as_its_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Refusing(_Client):
        async def __call__(self, request: object) -> object:
            raise errors.UserPrivacyRestrictedError(request=request)

    patch_action_client(monkeypatch, _Refusing())
    result = await execute("acc", MasstagToggleTtl(peer="42", hold_seconds=0))

    assert (result.status, result.error_type) == ("failed", "UserPrivacyRestrictedError")


@pytest.mark.asyncio
async def test_a_username_resolves_to_a_person(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_read_client(monkeypatch, _Client({"alpha": _user(5, "alpha")}))

    result = await execute_read("acc", ResolveMasstagTarget(target="alpha"))

    assert isinstance(result, MasstagResolvedTarget)
    assert result.outcome == "user"
    assert result.user is not None
    assert (result.user.user_id, result.user.username) == (5, "alpha")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing",
    [
        errors.UsernameNotOccupiedError(request=None),
        errors.UsernameInvalidError(request=None),
        None,
    ],
)
async def test_nobody_by_that_name_is_not_found(
    monkeypatch: pytest.MonkeyPatch, missing: Exception | None
) -> None:
    entities: dict[object, object] = {} if missing is None else {"ghost": missing}
    patch_read_client(monkeypatch, _Client(entities))

    result = await execute_read("acc", ResolveMasstagTarget(target="ghost"))

    assert result == MasstagResolvedTarget(outcome="not_found")


@pytest.mark.asyncio
async def test_a_group_answers_with_its_members(monkeypatch: pytest.MonkeyPatch) -> None:
    group = Chat(
        id=9, title="g", photo=ChatPhotoEmpty(), participants_count=2, date=None, version=1
    )
    client = _Client({"moscow_chat": group})
    client.participants = [_user(1, "alpha"), _user(2), SimpleNamespace(id=3)]
    patch_read_client(monkeypatch, client)

    result = await execute_read("acc", ResolveMasstagTarget(target="moscow_chat"))

    assert isinstance(result, MasstagResolvedTarget)
    assert result.outcome == "group"
    assert [(u.user_id, u.username) for u in result.members] == [(1, "alpha"), (2, None)]


@pytest.mark.asyncio
async def test_a_channel_is_not_a_person_and_a_hidden_group_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = Channel(
        id=8, title="c", photo=ChatPhotoEmpty(), date=None, broadcast=True, access_hash=1
    )
    group = Channel(
        id=9, title="g", photo=ChatPhotoEmpty(), date=None, megagroup=True, access_hash=1
    )
    client = _Client({"news": channel, "closed": group})
    client.participants = errors.ChatAdminRequiredError(request=None)
    patch_read_client(monkeypatch, client)

    news = await execute_read("acc", ResolveMasstagTarget(target="news"))
    closed = await execute_read("acc", ResolveMasstagTarget(target="closed"))

    assert news == MasstagResolvedTarget(outcome="not_user")
    assert closed == MasstagResolvedTarget(outcome="hidden")


@pytest.mark.asyncio
async def test_contacts_are_read_as_people(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_read_client(monkeypatch, _Client())

    result = await execute_read("acc", ListMasstagContacts())

    assert isinstance(result, MasstagContactsResult)
    assert [(u.user_id, u.is_bot) for u in result.users] == [(1, False), (2, False), (3, True)]


def test_mentions_are_appended_with_utf16_offsets() -> None:
    caption, spans = caption_with_mentions(
        "Урок 🎓",
        [StoryMention(user_id=1, username="alpha"), StoryMention(user_id=2, first_name="Оля")],
    )

    assert caption == "Урок 🎓 @alpha Оля"
    # The emoji is two UTF-16 units, so "@alpha" starts at 8, not 7.
    assert [(s.offset, s.length, s.user_id) for s in spans] == [(8, 6, 1), (15, 3, 2)]


@pytest.mark.asyncio
async def test_a_story_carries_mention_entities_and_a_link_area(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client()
    patch_action_client(monkeypatch, client)

    result = await execute(
        "acc",
        PostStory(
            filename="story.jpg",
            content=_jpeg(),
            media_kind="image",
            caption="Hi",
            privacy_preset="public",
            period_seconds=172_800,
            mentions=[StoryMention(user_id=11, username="alpha")],
            link=StoryLink(url="https://t.me/school", text="Go"),
        ),
    )

    assert result.status == "ok"
    sent = next(r for r in client.requests if isinstance(r, SendStoryRequest))
    assert sent.caption == "Hi @alpha"
    assert sent.period == 172_800
    assert sent.entities is not None
    assert sent.media_areas is not None
    entity = sent.entities[0]
    assert isinstance(entity, InputMessageEntityMentionName)
    assert (entity.offset, entity.length) == (3, 6)
    area = sent.media_areas[0]
    assert isinstance(area, MediaAreaUrl)
    assert area.url == "https://t.me/school"


@pytest.mark.asyncio
async def test_a_plain_story_has_no_entities_nor_areas(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client()
    patch_action_client(monkeypatch, client)

    await execute(
        "acc", PostStory(filename="story.jpg", content=_jpeg(), media_kind="image", caption="x")
    )

    sent = next(r for r in client.requests if isinstance(r, SendStoryRequest))
    assert (sent.entities, sent.media_areas, sent.caption) == (None, None, "x")


def test_the_link_plate_is_drawn_only_with_a_usable_font(monkeypatch: pytest.MonkeyPatch) -> None:
    content = _jpeg()
    monkeypatch.setattr("core.telegram_client._story_link._FONT_CANDIDATES", ())

    assert draw_link_plate(content, "Go") == content
    assert draw_link_plate(content, "   ") == content


def test_the_link_plate_is_drawn_where_the_area_is() -> None:
    from core.telegram_client._story_link import _FONT_CANDIDATES  # noqa: PLC0415

    content = _jpeg()
    drawn = draw_link_plate(content, "Go")

    if any(Path(candidate).is_file() for candidate in _FONT_CANDIDATES):
        assert drawn != content
        with Image.open(BytesIO(drawn)) as image:
            # The plate is white and centred where ``LINK_AREA`` puts the link.
            assert image.getpixel((200 - 100, int(600 * 0.82))) == (255, 255, 255)
    else:
        assert drawn == content
