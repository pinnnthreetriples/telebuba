"""Tests for ``services.neurocomment.board`` — the work-view read model.

Seeds real DB rows (campaign, channels, accounts, readiness, posted comments)
and asserts the assembled board: per-account quota usage + health and the
per-channel aggregate status derivation. Mirrors the warming board tests'
seed-then-assert approach.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    assign_account_to_campaign,
    claim_comment,
    configure_database,
    create_account,
    create_campaign,
    insert_challenge,
    link_channel_to_campaign,
    mark_comment_posted,
    upsert_linked_group,
    upsert_readiness,
)
from core.logging import reset_logging_for_tests, setup_logging
from core.repositories.neurocomment import set_campaign_account_channel
from schemas.accounts import AccountCreate
from schemas.challenge import ChallengeInsert
from schemas.neurocomment import CampaignCreate
from services.neurocomment import _state
from services.neurocomment.board import load_neurocomment_board

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    _state.reset_for_tests()  # challenge back-off is module-global; isolate per test
    reset_logging_for_tests()
    setup_logging()


async def _post_comment(channel: str, post_id: int, campaign_id: str, account_id: str) -> None:
    await claim_comment(channel, post_id, campaign_id, account_id)
    await mark_comment_posted(channel, post_id, comment_text="hi", comment_msg_id=post_id)


@pytest.mark.asyncio
async def test_unknown_campaign_returns_none() -> None:
    assert await load_neurocomment_board("nope") is None


@pytest.mark.asyncio
async def test_board_basic_shape() -> None:
    campaign = await create_campaign(CampaignCreate(name="C1", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1", label="Account One"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.campaign_name == "C1"
    assert board.status == "active"
    assert len(board.accounts) == 1
    card = board.accounts[0]
    assert card.account_id == "acc-1"
    assert card.label == "Account One"
    assert card.max_comments_per_hour == settings.neurocomment.max_comments_per_hour
    assert [r.channel for r in card.readiness] == ["@chan"]
    assert len(board.channels) == 1
    assert board.channels[0].channel == "@chan"
    assert board.channels[0].status == "ready"
    assert board.channels[0].ready_accounts == 1
    assert board.channels[0].total_accounts == 1


@pytest.mark.asyncio
async def test_card_carries_pinned_channel_and_null_when_unpinned() -> None:
    """A pinned account's card reports its channel; an unpinned one reports None."""
    campaign = await create_campaign(CampaignCreate(name="C1", prompt="p"))
    await create_account(AccountCreate(account_id="pinned", label="Pinned"))
    await create_account(AccountCreate(account_id="free", label="Free"))
    await assign_account_to_campaign(campaign.campaign_id, "pinned")
    await assign_account_to_campaign(campaign.campaign_id, "free")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await set_campaign_account_channel(campaign.campaign_id, "pinned", "@chan")

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    pins = {card.account_id: card.pinned_channel for card in board.accounts}
    assert pins == {"pinned": "@chan", "free": None}


@pytest.mark.asyncio
async def test_card_counts_today_and_last_hour() -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    # Two posted comments, both within the day; both within the hour by default.
    await _post_comment("@chan", 1, campaign.campaign_id, "acc-1")
    await _post_comment("@chan", 2, campaign.campaign_id, "acc-1")

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    card = board.accounts[0]
    assert card.comments_today == 2
    assert card.comments_last_hour == 2
    assert card.last_comment_at is not None
    assert card.last_comment_text == "hi"


@pytest.mark.asyncio
async def test_old_comment_excluded_from_day_window() -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await _post_comment("@chan", 1, campaign.campaign_id, "acc-1")
    # Backdate the row 2 days so it falls outside the day window.
    from core.db import _get_engine  # noqa: PLC0415 - test-only direct backdate.

    old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_comments SET created_at = ? WHERE post_id = 1",
            (old,),
        )

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.accounts[0].comments_today == 0


@pytest.mark.asyncio
async def test_channel_status_comments_off() -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_linked_group("@chan", None, comments_enabled=False)

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "comments_off"


@pytest.mark.asyncio
async def test_channel_status_chat_restricted() -> None:
    # Ф2 #120 state split + conservative remap: a joined-but-write-blocked row
    # (the pre-Ф2 captcha_gated boolean shape) now derives as ``chat_restricted``.
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=False, ready=False)

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "chat_restricted"


@pytest.mark.asyncio
async def test_channel_status_bot_challenge_when_challenge_row_exists() -> None:
    # Same joined-but-not-ready shape as chat_restricted, but a guardian-bot
    # challenge row was recorded → the board distinguishes it as bot_challenge.
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=False, ready=False)
    await insert_challenge(
        ChallengeInsert(
            challenge_hash="h1",
            account_id="acc-1",
            channel="@chan",
            raw_text="prove you are human",
            button_labels=["Я человек"],
            outcome="give_up",
        ),
    )

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "bot_challenge"


@pytest.mark.asyncio
async def test_channel_status_bot_challenge_backoff() -> None:
    # Ф2 #147: a channel in challenge back-off shows bot_challenge_backoff (paused),
    # taking precedence over readiness.
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    _state.register_challenge_failure(
        "@chan", datetime.now(UTC), min_failures=1, base_seconds=3600, max_seconds=86400
    )

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "bot_challenge_backoff"


@pytest.mark.asyncio
async def test_channel_status_join_by_request() -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_readiness("acc-1", "@chan", joined=False, captcha_passed=False, ready=False)

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "join_by_request"


@pytest.mark.asyncio
async def test_channel_status_join_failed_is_distinct_from_join_by_request() -> None:
    # A hard-failed join (invalid invite / banned) must NOT render as "awaiting
    # approval". Onboarding persists a distinct signal (captcha_passed on an unjoined
    # row) that the board maps to join_failed, leaving the approval-gate row untouched.
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    # The terminal-failure readiness shape onboarding writes for a hard fail.
    await upsert_readiness("acc-1", "@chan", joined=False, captcha_passed=True, ready=False)

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "join_failed"


@pytest.mark.asyncio
async def test_channel_status_throttled_when_no_rows() -> None:
    # No readiness rows at all (no account joined) and comments are enabled →
    # nothing ready, no specific gate → throttled fallback.
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await upsert_linked_group("@chan", 123, comments_enabled=True)

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.channels[0].status == "throttled"


@pytest.mark.asyncio
async def test_card_readiness_scoped_to_this_campaigns_channels() -> None:
    # An account in two campaigns must show only THIS campaign's (account, channel)
    # readiness on its card — not the other campaign's channel chips.
    acc = "acc-1"
    await create_account(AccountCreate(account_id=acc))
    this_campaign = await create_campaign(CampaignCreate(name="This", prompt="p"))
    other_campaign = await create_campaign(CampaignCreate(name="Other", prompt="p"))
    await assign_account_to_campaign(this_campaign.campaign_id, acc)
    await assign_account_to_campaign(other_campaign.campaign_id, acc)
    await link_channel_to_campaign(this_campaign.campaign_id, "@mine")
    await link_channel_to_campaign(other_campaign.campaign_id, "@theirs")
    await upsert_readiness(acc, "@mine", joined=True, captcha_passed=True, ready=True)
    await upsert_readiness(acc, "@theirs", joined=True, captcha_passed=True, ready=True)

    board = await load_neurocomment_board(this_campaign.campaign_id)

    assert board is not None
    assert [r.channel for r in board.accounts[0].readiness] == ["@mine"]


@pytest.mark.asyncio
async def test_account_health_blocked_for_new_account() -> None:
    # A fresh account (status "new", no proxy) is not warming-ready → blocked.
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert board.accounts[0].health == "blocked"
