"""Tests for ``bans.confirm_group_ban_and_leave`` — the per-group ban ladder.

``UserBannedInChannelError`` is Telegram's account-wide write restriction, so it
alone must never park a pair. Only a ``restricted`` participant record in the
group PLUS a ``clean`` @SpamBot verdict marks the pair banned and leaves the
group. These tests drive the helper directly and then check the two error-branch
callers (post time and join time) route through it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from core.db import (
    assign_account_to_campaign,
    create_account,
    create_campaign,
    fetch_readiness,
    link_channel_to_campaign,
    list_recent_logs,
    upsert_readiness,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.spam_status import SpamStatusVerdict
from schemas.telegram_actions import ActionResult, BanCheckResult, NewPostEvent
from services.neurocomment import _seams, _state, bans, engine, onboarding
from tests.services.neurocomment.engine_support import (
    _CommentStub,
    _make_campaign,
    _patch_ban_confirmation,
    _patch_io,
)
from tests.services.neurocomment.onboarding_support import _JoinStub, _ReadStub

if TYPE_CHECKING:
    from schemas.spam_status import SpamStatusKind
    from schemas.telegram_actions import TelegramAction, TelegramReadAction
    from tests.services.neurocomment.onboarding_support import _BanState


class _LeaveStub:
    """Captures ``_seams.execute`` calls; optionally raises on the leave."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, TelegramAction]] = []

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        self.calls.append((account_id, action))
        if self.error is not None:
            raise self.error
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)


def _patch_ladder(
    monkeypatch: pytest.MonkeyPatch,
    *,
    state: _BanState | Exception = "restricted",
    spam: SpamStatusKind = "clean",
    leave: _LeaveStub | None = None,
) -> _LeaveStub:
    async def _read(_account_id: str, _action: TelegramReadAction) -> BanCheckResult:
        if isinstance(state, Exception):
            raise state
        return BanCheckResult(state=state)

    async def _spam(account_id: str, **_kwargs: object) -> SpamStatusVerdict:
        return SpamStatusVerdict(
            account_id=account_id, status=spam, checked_at="2026-01-01T00:00:00"
        )

    stub = leave or _LeaveStub()
    monkeypatch.setattr(_seams, "execute_read", _read)
    monkeypatch.setattr(_seams, "refresh_spam_status", _spam)
    monkeypatch.setattr(_seams, "execute", stub.execute)
    return stub


async def _seed_pair() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)


async def _banned(account_id: str = "acc-1", channel: str = "@chan") -> bool:
    readiness = await fetch_readiness(account_id, channel)
    assert readiness is not None
    return readiness.banned


async def _logged(code: str) -> bool:
    return any(entry.event == code for entry in await list_recent_logs(limit=200))


# --------------------------------------------------------------------------- #
# The ladder itself
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine")
async def test_restricted_and_clean_marks_the_pair_and_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The only confirming combination: the group restricted us, the account is clean."""
    await _seed_pair()
    leave = _patch_ladder(monkeypatch)

    assert await bans.confirm_group_ban_and_leave("acc-1", "@chan") is True

    assert await _banned() is True
    assert [action.action_type for _, action in leave.calls] == ["leave_discussion_group"]
    assert await _logged("neurocomment_group_ban_confirmed")


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine")
async def test_limited_account_is_not_a_group_ban(monkeypatch: pytest.MonkeyPatch) -> None:
    """@SpamBot says limited → the write block is account-wide, the group is innocent."""
    await _seed_pair()
    leave = _patch_ladder(monkeypatch, spam="limited")

    assert await bans.confirm_group_ban_and_leave("acc-1", "@chan") is False

    assert await _banned() is False
    assert leave.calls == []
    assert await _logged("neurocomment_group_ban_account_limited")


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine")
async def test_unknown_spam_verdict_is_not_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe never reached @SpamBot — be conservative, nothing is proven."""
    await _seed_pair()
    leave = _patch_ladder(monkeypatch, spam="unknown")

    assert await bans.confirm_group_ban_and_leave("acc-1", "@chan") is False

    assert await _banned() is False
    assert leave.calls == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine")
@pytest.mark.parametrize("state", ["not_member", "can_send", "comments_disabled"])
async def test_only_restricted_confirms(monkeypatch: pytest.MonkeyPatch, state: _BanState) -> None:
    """not_member collapses kicked / never-joined / left — it is not proof of a ban."""
    await _seed_pair()
    leave = _patch_ladder(monkeypatch, state=state)

    assert await bans.confirm_group_ban_and_leave("acc-1", "@chan") is False

    assert await _banned() is False
    assert leave.calls == []
    assert await _logged("neurocomment_group_ban_unconfirmed")


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine")
async def test_probe_fault_confirms_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    await _seed_pair()
    leave = _patch_ladder(monkeypatch, state=RuntimeError("offline"))

    assert await bans.confirm_group_ban_and_leave("acc-1", "@chan") is False

    assert await _banned() is False
    assert leave.calls == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine")
async def test_failed_leave_keeps_the_mark_and_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ban is the truth: it is written before the leave and survives its failure."""
    await _seed_pair()
    _patch_ladder(monkeypatch, leave=_LeaveStub(error=RuntimeError("rpc down")))

    assert await bans.confirm_group_ban_and_leave("acc-1", "@chan") is True

    assert await _banned() is True
    assert await _logged("neurocomment_group_ban_confirmed")


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine")
async def test_known_state_skips_the_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 'Проверить каналы' button already paid for the probe — don't repeat it."""
    await _seed_pair()

    async def _boom(_account_id: str, _action: TelegramReadAction) -> BanCheckResult:
        msg = "the probe must not run when the state is known"
        raise AssertionError(msg)

    _patch_ladder(monkeypatch)
    monkeypatch.setattr(_seams, "execute_read", _boom)

    assert (
        await bans.confirm_group_ban_and_leave("acc-1", "@chan", known_state="restricted") is True
    )
    assert await _banned() is True


# --------------------------------------------------------------------------- #
# Callers
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine")
async def test_post_time_ban_without_confirmation_only_cools_the_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A globally limited account must not collect a sticky ban on this channel."""
    await _make_campaign("@chan", "acc-1")
    _patch_io(
        monkeypatch, comment=_CommentStub(status="failed", error_type="UserBannedInChannelError")
    )
    _patch_ban_confirmation(monkeypatch, spam="limited")

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=1, text="hi"))

    assert await _banned() is False
    # Parked on the duration-less cooldown so it cannot re-select and loop on the error.
    assert _state.in_cooldown("acc-1", datetime.now(UTC), "@chan") is True
    assert await _logged("neurocomment_post_ban_unconfirmed")


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_onboarding")
async def test_join_time_ban_without_confirmation_is_chat_restricted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No per-group proof → the readiness write stands, the sticky ban does not."""
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    read = _ReadStub(linked_chat_id=88, comments_enabled=True, ban_state="not_member")
    join = _JoinStub()
    join.set("@banned", status="failed", error_type="UserBannedInChannelError")
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)

    outcome = await onboarding.onboard_account_channel("acc-1", "@banned")

    assert outcome.state == "chat_restricted"
    readiness = await fetch_readiness("acc-1", "@banned")
    assert readiness is not None
    assert readiness.banned is False
    assert readiness.ready is False


@pytest.mark.asyncio
@pytest.mark.usefixtures("isolate_engine")
async def test_check_channels_button_leaves_a_confirmed_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator button runs the same ladder on its restricted verdicts."""
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await _seed_pair()
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    leave = _patch_ladder(monkeypatch)

    result = await bans.check_campaign_channel_bans(campaign.campaign_id)

    assert result is not None
    assert [item.status for item in result.items] == ["banned"]
    assert await _banned() is True
    assert [action.action_type for _, action in leave.calls] == ["leave_discussion_group"]
