"""A chat's own rules: a mute on one account, a chat closed to all, a chat without photos."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast import ChatBroadcastSettings, ChatBroadcastSettingsUpdate
from services import chat_broadcast as service
from tests.services.chat_broadcast.fakes import refused, run_to_end, seed

if TYPE_CHECKING:
    from tests.services.chat_broadcast.fakes import FakeTelegram

_MUTED = "ChatWriteForbiddenError"


@pytest.mark.asyncio
async def test_a_mute_on_one_account_moves_the_chat_to_another(telegram: FakeTelegram) -> None:
    campaign_id = await seed(accounts=("a1", "a2"), targets=("@alpha", "@beta"), messages=("Hi",))
    telegram.send_answers["a1"] = [refused(error_type=_MUTED)]
    telegram.muted.add("a1")

    await run_to_end(campaign_id)

    alpha = await repository.fetch_target(campaign_id, "alpha")
    assert alpha is not None
    assert (alpha.state, alpha.assigned_account_id, alpha.handed_from_account_id) == (
        "done",
        "a2",
        "a1",
    )
    # Sent exactly once, by the account that was not muted.
    assert sorted(s.account_id for s in telegram.sent) == ["a2", "a2"]
    journal = await repository.list_journal(campaign_id)
    assert [(row.account_id, row.status) for row in journal if row.chat_key == "alpha"] == [
        ("a2", "sent")
    ]


@pytest.mark.asyncio
async def test_a_chat_closed_to_everybody_is_skipped_without_moving(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(accounts=("a1", "a2"), targets=("@alpha", "@beta"), messages=("Hi",))
    telegram.send_answers["a1"] = [refused(error_type=_MUTED)]

    await run_to_end(campaign_id)

    alpha = await repository.fetch_target(campaign_id, "alpha")
    assert alpha is not None
    assert (alpha.state, alpha.skip_reason, alpha.handed_from_account_id) == (
        "skipped",
        "admin_only",
        None,
    )
    assert ("a2", "alpha") not in telegram.joins


@pytest.mark.asyncio
async def test_a_chat_that_mutes_the_second_account_too_is_skipped(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(accounts=("a1", "a2"), targets=("@alpha",), messages=("Hi",))
    telegram.send_answers["a1"] = [refused(error_type=_MUTED)]
    telegram.send_answers["a2"] = [refused(error_type=_MUTED)]
    telegram.muted.update({"a1", "a2"})

    await run_to_end(campaign_id)

    alpha = await repository.fetch_target(campaign_id, "alpha")
    assert alpha is not None
    assert (alpha.state, alpha.skip_reason) == ("skipped", "admin_only")
    assert telegram.sent == []


@pytest.mark.asyncio
async def test_a_chat_without_photos_gets_the_text_alone(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _photo(_media_id: str) -> bytes:
        return b"png"

    monkeypatch.setattr("services.chat_broadcast._context.read_photo", _photo)
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), messages=("caption",))
    current = await service.load_settings(campaign_id)
    assert current is not None
    photo = {"media_id": "a" * 64 + ".png", "name": "a.png"}
    messages = ChatBroadcastSettings.model_validate(
        {"messages": [{"text": "caption", "photo": photo}]}
    ).messages
    await service.save_settings(
        campaign_id,
        ChatBroadcastSettingsUpdate(
            expected_updated_at=current.updated_at,
            name="Crypto",
            account_ids=["a1"],
            settings=current.settings.model_copy(update={"messages": messages}),
        ),
    )
    telegram.send_answers["a1"] = [refused(error_type="ChatSendPhotosForbiddenError")]

    await run_to_end(campaign_id)

    assert [(s.text, s.photo) for s in telegram.sent] == [("caption", False)]
    journal = await repository.list_journal(campaign_id)
    assert [(row.kind, row.status) for row in journal] == [("text", "sent")]
    alpha = await repository.fetch_target(campaign_id, "alpha")
    assert alpha is not None
    assert alpha.state == "done"
