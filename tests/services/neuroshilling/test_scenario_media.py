"""Approval refuses a media source the accounts that must post it cannot see.

The copy is made by the account playing the media step, not by a designated
carrier — a message arriving from an account with no part in the scene is exactly
the tell the staging exists to avoid. Whether that account can READ the source is a
live Telegram question, so it is asked once at approval instead of being discovered
mid-run in a stranger's chat.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from core.db import create_account
from core.repositories import neuroshilling as repository
from core.telegram_client import TelegramReadError
from schemas.accounts import AccountCreate
from schemas.neuroshilling import (
    NeuroshillingAccountAssignment,
    NeuroshillingCampaignCreate,
    NeuroshillingCampaignUpdate,
)
from schemas.neuroshilling_scenario import (
    NeuroshillingRoleInput,
    NeuroshillingScenarioUpdate,
    NeuroshillingStepInput,
)
from schemas.telegram_actions import ChatMessagePreview, ReadChatMessagesResult
from services import neuroshilling as ns_service
from services.neuroshilling import _seams

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingCampaign

_LINK = "https://t.me/c/1234567890/42"


async def _campaign_with_media(
    *,
    steps: list[NeuroshillingStepInput] | None = None,
    **fields: Any,
) -> NeuroshillingCampaign:
    """A campaign whose only role is played by ``acc-1``. One message step by default."""
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await repository.create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    stored_scenario = await ns_service.set_scenario(
        campaign.campaign_id,
        NeuroshillingScenarioUpdate(
            roles=[NeuroshillingRoleInput(role_id="a", name="Skeptic")],
            steps=steps or [NeuroshillingStepInput(role_id="a", text="look")],
        ),
    )
    assert stored_scenario is not None
    # The save MINTS role ids; the form's key is only how a step names its speaker.
    role_id = stored_scenario.roles[0].role_id
    payload: dict[str, Any] = {
        "name": "Promo",
        "topic": "delivery",
        "media_message_link": _LINK,
        "media_step_position": 1,
        "accounts": [NeuroshillingAccountAssignment(account_id="acc-1", role_id=role_id)],
        **fields,
    }
    updated = await repository.update_campaign(
        campaign.campaign_id,
        NeuroshillingCampaignUpdate(**payload),
    )
    assert updated is not None
    return updated


def _read(outcome: Any) -> Any:
    calls: list[Any] = []

    async def execute_read(account_id: str, action: Any) -> Any:
        calls.append((account_id, action))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    execute_read.calls = calls  # ty: ignore[unresolved-attribute]
    return execute_read


def _visible(kind: str) -> ReadChatMessagesResult:
    return ReadChatMessagesResult(
        messages=[ChatMessagePreview(message_id=42, media_kind=kind)],  # ty: ignore[invalid-argument-type]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["photo", "document"])
async def test_a_reachable_media_source_lets_the_scenario_be_approved(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    campaign = await _campaign_with_media()
    reader = _read(_visible(kind))
    monkeypatch.setattr(_seams, "execute_read", reader)

    approved = await ns_service.approve_scenario(campaign.campaign_id)

    assert approved is not None
    assert approved.scenario_status == "approved"
    # The private link's ``c/<internal>`` segment is the raw positive chat id, and it
    # travels as digits so the gateway feeds it to the session entity cache.
    account_id, action = reader.calls[0]
    assert (account_id, action.chat, action.message_ids) == ("acc-1", "1234567890", [42])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        # The account cannot see the message at all.
        ReadChatMessagesResult(missing_ids=[42]),
        # A web-page preview makes ``send_file`` raise; the empty union makes it send
        # a message with NO media, which is worse.
        _visible("web_page"),
        _visible("unsupported"),
        _visible("none"),
        TelegramReadError("chat_not_found"),
    ],
)
async def test_an_unreachable_media_source_refuses_the_approval(
    monkeypatch: pytest.MonkeyPatch,
    outcome: Any,
) -> None:
    campaign = await _campaign_with_media()
    monkeypatch.setattr(_seams, "execute_read", _read(outcome))

    with pytest.raises(ns_service.NeuroshillingInvalidError) as refusal:
        await ns_service.approve_scenario(campaign.campaign_id)

    assert refusal.value.code == "media_source_unreachable"
    stored = await repository.fetch_campaign(campaign.campaign_id)
    assert stored is not None
    assert stored.scenario_status == "draft"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["flood_wait", "unavailable"])
async def test_a_check_that_never_happened_is_not_a_verdict_on_the_link(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    """The account was never asked, so the link is not what needs fixing.

    Folded into the "cannot see it" refusal, a five-minute flood sent the operator to
    edit a link that was fine, and no edit could clear it.
    """
    campaign = await _campaign_with_media()
    monkeypatch.setattr(
        _seams,
        "execute_read",
        _read(TelegramReadError("read_failed", kind=kind)),  # ty: ignore[invalid-argument-type]
    )

    with pytest.raises(ns_service.NeuroshillingUnavailableError) as refusal:
        await ns_service.approve_scenario(campaign.campaign_id)

    assert refusal.value.code == "media_check_unavailable"


@pytest.mark.asyncio
async def test_an_unparseable_link_is_the_same_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unreachable is unreachable, and a second code would only split one fix in two."""
    campaign = await _campaign_with_media(media_message_link="https://t.me/nomessagehere")
    reader = _read(_visible("photo"))
    monkeypatch.setattr(_seams, "execute_read", reader)

    with pytest.raises(ns_service.NeuroshillingInvalidError) as refusal:
        await ns_service.approve_scenario(campaign.campaign_id)

    assert refusal.value.code == "media_source_unreachable"
    # Nothing was asked of Telegram: the link never named a message to look for.
    assert reader.calls == []


@pytest.mark.asyncio
async def test_a_campaign_without_media_never_touches_telegram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = await _campaign_with_media(media_message_link=None, media_step_position=None)
    reader = _read(_visible("photo"))
    monkeypatch.setattr(_seams, "execute_read", reader)

    approved = await ns_service.approve_scenario(campaign.campaign_id)

    assert approved is not None
    assert reader.calls == []


def _reaction_at_step_two() -> list[NeuroshillingStepInput]:
    """A message, then a reaction aimed at it — the media slot will name the reaction."""
    return [
        NeuroshillingStepInput(role_id="a", text="look"),
        NeuroshillingStepInput(role_id="a", kind="reaction", target_position=1, emoji="🔥"),
    ]


@pytest.mark.asyncio
async def test_media_pinned_to_a_reaction_step_refuses_the_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reaction cannot carry the media, and the run does not say so.

    ``_dispatch.media_source`` is consulted only where a MESSAGE goes out, so a slot
    aimed at a reaction posts no media and writes no log line while not posting it —
    the campaign reaches ``done`` and the operator is left with nothing to read. The
    card's picker now offers message steps only, so the state this refuses is the one
    nobody chose: a generation that replaced the steps under a slot still pointing at
    position 2.
    """
    campaign = await _campaign_with_media(steps=_reaction_at_step_two(), media_step_position=2)
    monkeypatch.setattr(_seams, "execute_read", _read(_visible("photo")))

    with pytest.raises(ns_service.NeuroshillingInvalidError) as refusal:
        await ns_service.approve_scenario(campaign.campaign_id)

    assert refusal.value.code == "media_step_not_message"
    stored = await repository.fetch_campaign(campaign.campaign_id)
    assert stored is not None
    assert stored.scenario_status == "draft"


@pytest.mark.asyncio
async def test_the_step_kind_is_judged_before_telegram_is_asked_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rows answer this one, so it must not cost a live read per account.

    ``_refuse_unreachable_media`` spends one Telegram read for every active account
    of the media step's role. A slot that is wrong on the rows alone is refused by
    ``_approval_problem`` first, which is what keeps those reads unspent.
    """
    campaign = await _campaign_with_media(steps=_reaction_at_step_two(), media_step_position=2)
    reader = _read(_visible("photo"))
    monkeypatch.setattr(_seams, "execute_read", reader)

    with pytest.raises(ns_service.NeuroshillingInvalidError):
        await ns_service.approve_scenario(campaign.campaign_id)

    assert reader.calls == []


@pytest.mark.asyncio
async def test_only_the_accounts_that_play_the_media_step_are_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bystander account's blindness is irrelevant — it will never send the media."""
    await create_account(AccountCreate(account_id="acc-2", label="B", session_name="acc-2"))
    campaign = await _campaign_with_media()
    roster = await repository.list_campaign_accounts(campaign.campaign_id)
    updated = await repository.update_campaign(
        campaign.campaign_id,
        NeuroshillingCampaignUpdate(
            name=campaign.name,
            media_message_link=_LINK,
            media_step_position=1,
            accounts=[
                NeuroshillingAccountAssignment(account_id="acc-1", role_id=roster[0].role_id),
                NeuroshillingAccountAssignment(account_id="acc-2", role_id=None),
            ],
        ),
    )
    assert updated is not None
    reader = _read(_visible("photo"))
    monkeypatch.setattr(_seams, "execute_read", reader)

    assert await ns_service.approve_scenario(campaign.campaign_id) is not None
    assert [account_id for account_id, _action in reader.calls] == ["acc-1"]
