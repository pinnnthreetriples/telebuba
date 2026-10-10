"""Campaign policy: the settings save, its refusals, who is busy; and link parsing."""

from __future__ import annotations

import random

import pytest

from core.db import create_account
from schemas.accounts import AccountCreate
from schemas.chat_broadcast import ChatBroadcastSettings, ChatBroadcastSettingsUpdate
from services import _account_owner
from services import chat_broadcast as service
from services.chat_broadcast import _state
from services.chat_broadcast._errors import ChatBroadcastConflictError, ChatBroadcastInvalidError
from services.chat_broadcast.campaigns import busy_owners
from services.chat_broadcast.links import has_variants, parse_target, render
from tests.services.chat_broadcast.fakes import seed


async def _update(campaign_id: str, **settings: object) -> ChatBroadcastSettingsUpdate:
    current = await service.load_settings(campaign_id)
    assert current is not None
    return ChatBroadcastSettingsUpdate(
        expected_updated_at=current.updated_at,
        name="Renamed",
        account_ids=current.account_ids,
        settings=ChatBroadcastSettings.model_validate(current.settings.model_dump() | settings),
    )


@pytest.mark.asyncio
async def test_create_list_save_and_delete() -> None:
    campaign_id = await seed(targets=("@alpha", "t.me/alpha", "@beta"))

    saved = await service.save_settings(campaign_id, await _update(campaign_id))
    [listed] = (await service.list_campaigns()).items

    assert saved is not None
    assert saved.name == "Renamed"
    # The same chat twice is one chat.
    assert saved.settings.targets == ["@alpha", "@beta"]
    assert (listed.status, listed.account_count, listed.target_count) == ("draft", 2, 2)
    assert await service.delete_campaign(campaign_id)
    assert await service.delete_campaign(campaign_id) is None
    assert await service.load_settings(campaign_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"targets": ["not a link!"]}, "invalid_target"),
        ({"targets": ["t.me/addlist"]}, "invalid_target"),
        ({"messages": [{"kind": "post", "post": "t.me/channel"}]}, "invalid_post_link"),
        ({"own_excluded": ["abc"]}, "invalid_target"),
    ],
)
async def test_save_refuses_what_no_run_could_use(change: dict[str, object], code: str) -> None:
    campaign_id = await seed()

    with pytest.raises(ChatBroadcastInvalidError, match=code):
        await service.save_settings(campaign_id, await _update(campaign_id, **change))


@pytest.mark.asyncio
async def test_save_refuses_unknown_accounts_and_too_many_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = await seed()
    update = await _update(campaign_id)
    with pytest.raises(ChatBroadcastInvalidError, match="account_not_found"):
        await service.save_settings(
            campaign_id, update.model_copy(update={"account_ids": ["ghost"]})
        )
    monkeypatch.setattr("core.config.settings.chat_broadcast.max_targets_per_campaign", 1)
    with pytest.raises(ChatBroadcastInvalidError, match="too_many_targets"):
        await service.save_settings(campaign_id, update)
    monkeypatch.setattr("core.config.settings.chat_broadcast.max_accounts_per_campaign", 1)
    with pytest.raises(ChatBroadcastInvalidError, match="too_many_accounts"):
        await service.save_settings(campaign_id, update)


@pytest.mark.asyncio
async def test_save_refuses_a_stale_stamp_and_a_start_in_flight() -> None:
    campaign_id = await seed()
    update = await _update(campaign_id)
    await service.save_settings(campaign_id, update)

    with pytest.raises(ChatBroadcastConflictError, match="campaign_changed"):
        await service.save_settings(campaign_id, update)
    assert _state.try_claim_start(campaign_id)
    with pytest.raises(ChatBroadcastConflictError, match="campaign_running"):
        await service.save_settings(campaign_id, await _update(campaign_id))
    _state.finish_start(campaign_id)
    assert await service.save_settings("missing", update) is None


@pytest.mark.asyncio
async def test_busy_owners_name_every_other_holder() -> None:
    campaign_id = await seed(accounts=("a1", "a2", "a3"))
    for account_id in ("a4",):
        await create_account(
            AccountCreate(account_id=account_id, label=account_id, session_name=account_id)
        )
    _account_owner.try_claim("a1", "warming", "w-1")
    _account_owner.try_claim("a2", "chat_broadcast", campaign_id)
    _account_owner.try_claim("a3", "chat_broadcast", "other-campaign")

    busy = await busy_owners(campaign_id, ["a1", "a2", "a3", "a4"])

    assert busy == {"a1": "warming", "a3": "chat_broadcast"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("@Alpha", ("alpha", "public", "Alpha")),
        ("https://t.me/alpha_chat", ("alpha_chat", "public", "alpha_chat")),
        ("t.me/+AbCdEfGh123", ("+AbCdEfGh123", "invite", "+AbCdEfGh123")),
        ("t.me/joinchat/AbCdEfGh123", ("+AbCdEfGh123", "invite", "+AbCdEfGh123")),
        ("t.me/addlist/kq3Hf0dX", ("addlist:kq3Hf0dX", "folder", "kq3Hf0dX")),
        ("t.me/addlist/", None),
        ("hello world", None),
    ],
)
def test_parse_target(value: str, expected: tuple[str, str, str] | None) -> None:
    parsed = parse_target(value, max_length=64)
    assert (None if parsed is None else tuple(parsed)) == expected


def test_render_picks_variants_and_fills_variables() -> None:
    rng = random.Random(1)  # noqa: S311 - deterministic test choice
    text = render(
        "{Hi|Hey} {group_title}, see {group_username} {a|{b|c}}",
        title="Crypto",
        username="crypto",
        rng=rng,
    )

    assert text.split()[0] in {"Hi", "Hey"}
    assert "Crypto, see @crypto" in text
    assert text.split()[-1] in {"a", "b", "c"}
    assert render("{group_username}!", title=None, username=None, rng=rng) == "!"
    assert has_variants("{a|b}")
    assert not has_variants("{a}")


@pytest.mark.asyncio
async def test_a_caption_longer_than_telegram_allows_is_refused() -> None:
    campaign_id = await seed()
    photo = {"media_id": "a" * 64 + ".png", "name": "a.png"}

    with pytest.raises(ChatBroadcastInvalidError, match="caption_too_long"):
        await service.save_settings(
            campaign_id,
            await _update(campaign_id, messages=[{"text": "x" * 1025, "photo": photo}]),
        )
