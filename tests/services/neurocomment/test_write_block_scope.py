"""A refused write is asked WHOSE doing it is, and the answer picks the rule that acts.

``ChatWriteForbiddenError`` used to be one state, ``chat_restricted``, for two situations
needing opposite responses: a chat closed to EVERYONE (the account is innocent, the channel
is what should leave service) and a mute an admin put on THIS ONE account (nothing is wrong
with the channel, and Telegram carries the expiry). Indistinguishable, a temporary mute read
exactly like a lost captcha — same readiness triple, no challenge row — and the captcha rule
walked the pair out of the group for good.

These tests pin the three answers and, just as hard, the two things that must NOT change: the
probe fires only on an actual refusal, and an unreadable answer leaves behaviour exactly as
it was. The gateway half lives in ``tests/core/telegram_client/test_read_channels.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    assign_account_to_campaign,
    create_account,
    create_campaign,
    fetch_readiness,
    link_channel_to_campaign,
    list_campaign_channels,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.telegram_actions_rights import CheckWriteRights, WriteRightsResult
from services.neurocomment import _seams, _state, onboarding
from tests.services.neurocomment.onboarding_support import _JoinStub, _ReadStub

if TYPE_CHECKING:
    from schemas.telegram_actions import TelegramReadAction

pytestmark = pytest.mark.usefixtures("isolate_onboarding")

_CHANNEL = "@gated"


class _RightsRead(_ReadStub):
    """The onboarding read stub plus a canned (or raising) answer to ``CheckWriteRights``.

    Records every account the probe was asked about, because "one read per refusal, and
    none otherwise" is half of what this rule promises.
    """

    def __init__(self, rights: WriteRightsResult | Exception) -> None:
        super().__init__(linked_chat_id=88, comments_enabled=True)
        self.rights = rights
        self.rights_calls: list[str] = []

    async def execute_read(self, account_id: str, action: TelegramReadAction) -> object:
        if isinstance(action, CheckWriteRights):
            self.rights_calls.append(account_id)
            if isinstance(self.rights, Exception):
                raise self.rights
            return self.rights
        return await super().execute_read(account_id, action)


async def _campaign(*accounts: str) -> str:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, _CHANNEL)
    for account_id in accounts:
        await create_account(AccountCreate(account_id=account_id, session_name=account_id))
        await assign_account_to_campaign(campaign.campaign_id, account_id)
    return campaign.campaign_id


async def _channel_is_active(campaign_id: str) -> bool:
    links = (await list_campaign_channels(campaign_id)).links
    return any(link.channel == _CHANNEL and link.active for link in links)


def _refused_join(
    monkeypatch: pytest.MonkeyPatch,
    rights: WriteRightsResult | Exception,
    *,
    error_type: str = "ChatWriteForbiddenError",
) -> tuple[_RightsRead, _JoinStub]:
    """A join Telegram refuses with a write gate, over the given write-rights answer."""
    read = _RightsRead(rights)
    join = _JoinStub()
    join.set(_CHANNEL, status="failed", error_type=error_type)
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    return read, join


# --------------------------------------------------------------------------- #
# Everyone is muted: the CHANNEL leaves service, the account is untouched.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_chat_closed_to_everyone_takes_the_channel_out_of_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Routed into ``_comments_off``, the rule that already owns "comments are off here".

    Not a second channel-drop path: that module reports AND unlinks through the service, so
    a running listener reconciles and stops watching the channel. The account keeps its
    readiness row exactly as before — it did nothing wrong, and nothing terminal is written.
    """
    campaign_id = await _campaign("acc-1")
    read, _join = _refused_join(monkeypatch, WriteRightsResult(scope="everyone"))

    outcome = await onboarding.onboard_account_channel("acc-1", _CHANNEL)

    assert (outcome.state, outcome.reason) == ("comments_off", "write_blocked_for_everyone")
    assert await _channel_is_active(campaign_id) is False
    assert read.rights_calls == ["acc-1"]
    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert (row.joined, row.captcha_passed, row.ready) == (True, False, False)
    assert (row.banned, row.captcha_gave_up) == (False, False)


# --------------------------------------------------------------------------- #
# Only we are muted: nothing spent, nobody leaves, the expiry is the window.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_mute_on_this_account_alone_is_waited_out_not_punished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The channel is innocent, so it stays linked; the pair simply waits for the expiry.

    The cooldown is what makes the wait real. The readiness row alone stops selection only
    until the next onboarding pass re-joins as ``already_participant``, finds no challenge
    and writes ``ready`` back — and that pair's next refused post would spend the CHANNEL a
    round for a mute that was never the channel's doing.
    """
    campaign_id = await _campaign("acc-1")
    until = datetime.now(UTC) + timedelta(hours=3)
    read, _join = _refused_join(
        monkeypatch,
        WriteRightsResult(scope="self_only", muted_until=until.isoformat()),
    )

    outcome = await onboarding.onboard_account_channel("acc-1", _CHANNEL)

    assert outcome.state == "chat_restricted"
    assert outcome.reason == f"muted_until:{until.isoformat()}"
    assert read.rights_calls == ["acc-1"]
    assert await _channel_is_active(campaign_id) is True
    # Held until the mute lapses, and released by itself the moment it has.
    assert _state.in_cooldown("acc-1", until - timedelta(minutes=1), _CHANNEL) is True
    assert _state.in_cooldown("acc-1", until + timedelta(minutes=1), _CHANNEL) is False
    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert (row.joined, row.ready, row.captcha_gave_up) == (True, False, False)


@pytest.mark.asyncio
async def test_a_mute_that_never_expires_is_bounded_by_the_shared_timeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A permanent restriction carries no date, and "wait forever" is not a wait.

    Bounded by ``channel_pause_hours * channel_max_rounds`` — the 48h every sibling rule
    counts out, so the operator tunes one number — after which the pair is simply tried
    again and the hold re-armed if the mute still stands.
    """
    await _campaign("acc-1")
    nc = settings.neurocomment
    horizon = timedelta(hours=nc.channel_pause_hours * nc.channel_max_rounds)
    _read, _join = _refused_join(monkeypatch, WriteRightsResult(scope="self_only"))

    await onboarding.onboard_account_channel("acc-1", _CHANNEL)

    now = datetime.now(UTC)
    assert _state.in_cooldown("acc-1", now + horizon - timedelta(minutes=1), _CHANNEL) is True
    assert _state.in_cooldown("acc-1", now + horizon + timedelta(minutes=1), _CHANNEL) is False


@pytest.mark.asyncio
async def test_a_mute_dated_years_out_is_clamped_to_the_same_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Some clients send a far-future sentinel instead of "forever"; it means the same.

    Waiting to 2038 would park the pair past the life of the campaign and never re-check a
    mute an admin may lift tomorrow.
    """
    await _campaign("acc-1")
    nc = settings.neurocomment
    horizon = timedelta(hours=nc.channel_pause_hours * nc.channel_max_rounds)
    _read, _join = _refused_join(
        monkeypatch,
        WriteRightsResult(scope="self_only", muted_until="2038-01-19T03:14:07+00:00"),
    )

    await onboarding.onboard_account_channel("acc-1", _CHANNEL)

    now = datetime.now(UTC)
    assert _state.in_cooldown("acc-1", now + horizon + timedelta(minutes=1), _CHANNEL) is False


# --------------------------------------------------------------------------- #
# No answer: today's behaviour, unchanged. An unknown must never become a verdict.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rights", "expected_reason"),
    [
        (WriteRightsResult(scope="unknown", reason="no_linked_group"), "no_linked_group"),
        (WriteRightsResult(scope="none"), None),
    ],
)
async def test_an_answer_that_blames_nobody_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    rights: WriteRightsResult,
    expected_reason: str | None,
) -> None:
    """``chat_restricted`` and no action — the exact state this branch wrote before."""
    campaign_id = await _campaign("acc-1")
    _read, _join = _refused_join(monkeypatch, rights)

    outcome = await onboarding.onboard_account_channel("acc-1", _CHANNEL)

    assert (outcome.state, outcome.reason) == ("chat_restricted", expected_reason)
    assert await _channel_is_active(campaign_id) is True
    assert _state.in_cooldown("acc-1", datetime.now(UTC), _CHANNEL) is False


@pytest.mark.asyncio
async def test_a_probe_that_dies_leaves_the_pair_exactly_as_it_was(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mistake this whole rule exists to prevent: an unknown turning into a verdict.

    The readiness row is written BEFORE the probe for the same reason — the pair must stop
    being selected whatever the probe does, including dying.
    """
    campaign_id = await _campaign("acc-1")
    _read, _join = _refused_join(monkeypatch, RuntimeError("gateway down"))

    outcome = await onboarding.onboard_account_channel("acc-1", _CHANNEL)

    assert (outcome.state, outcome.reason) == ("chat_restricted", "RuntimeError")
    assert await _channel_is_active(campaign_id) is True
    assert _state.in_cooldown("acc-1", datetime.now(UTC), _CHANNEL) is False
    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert (row.joined, row.captcha_passed, row.ready) == (True, False, False)


# --------------------------------------------------------------------------- #
# The budget: one read, and only on an actual refusal.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_join_that_is_not_refused_pays_for_no_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never speculative, never on a schedule: an account frozen for chatter is the risk.

    A successful join and a rate limit both leave the probe unspent — only the write gate
    buys it, and it buys exactly one.
    """
    await _campaign("acc-1")
    read = _RightsRead(WriteRightsResult(scope="self_only"))
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)

    await onboarding.onboard_account_channel("acc-1", _CHANNEL)

    assert read.rights_calls == []


@pytest.mark.asyncio
async def test_the_other_gate_error_buys_the_same_single_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ChatGuestSendForbiddenError`` is the same wall, so it asks the same question once."""
    await _campaign("acc-1")
    read, _join = _refused_join(
        monkeypatch,
        WriteRightsResult(scope="everyone"),
        error_type="ChatGuestSendForbiddenError",
    )

    outcome = await onboarding.onboard_account_channel("acc-1", _CHANNEL)

    assert outcome.state == "comments_off"
    assert read.rights_calls == ["acc-1"]
