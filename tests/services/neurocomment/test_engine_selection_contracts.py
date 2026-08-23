"""Selection and quota contracts at exact boundaries."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.db import fetch_active_campaign_for_channel
from schemas.accounts import AccountRead
from schemas.neurocomment import NeurocommentReadiness, NeurocommentSettings
from schemas.neurocomment_limits import EffectiveAccountLimits
from services import _account_owner
from services.neurocomment import _gates, _pair_status, _state, engine
from services.neurocomment.settings_store import load_settings as load_neuro_settings
from tests.services.neurocomment.engine_support import _make_campaign

pytestmark = pytest.mark.usefixtures("isolate_engine")


def _caps(*, hourly: int = 5, daily: int = 3) -> EffectiveAccountLimits:
    """The per-account caps the quota gate now reads, resolved (#58)."""
    return EffectiveAccountLimits(
        max_joins_per_day=20,
        max_comments_per_hour=hourly,
        max_comments_per_channel_per_day=daily,
    )


def _limits(*, hourly: int = 5, daily: int = 3) -> NeurocommentSettings:
    return NeurocommentSettings(
        max_comments_per_hour=hourly,
        max_comments_per_channel_per_day=daily,
        reply_delay_min_seconds=0,
        reply_delay_max_seconds=1,
        min_trust_score=0,
        updated_at="2026-01-01T00:00:00+00:00",
    )


@pytest.mark.parametrize(
    ("hourly", "daily", "expected"),
    [
        (4, 2, None),
        (5, 2, "quota_hour"),
        (6, 3, "quota_hour"),
        (4, 3, "quota_day"),
    ],
)
def test_quota_boundaries(hourly: int, daily: int, expected: str | None) -> None:
    assert (
        _gates._quota_block_reason("account", _caps(), {"account": hourly}, {"account": daily})
        == expected
    )


def test_zero_daily_cap_is_an_off_switch() -> None:
    assert (
        _gates._quota_block_reason("account", _caps(daily=0), {"account": 0}, {"account": 999})
        is None
    )


def _row(account_id: str = "account", **overrides: object) -> NeurocommentReadiness:
    fields: dict[str, object] = {
        "account_id": account_id,
        "channel": "@channel",
        "joined": True,
        "captcha_passed": True,
        "ready": True,
        "checked_at": "2026-01-01T00:00:00+00:00",
    }
    return NeurocommentReadiness.model_validate(fields | overrides)


def _pool() -> engine._SelectionPool:
    account = AccountRead(
        account_id="account",
        status="alive",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    return engine._SelectionPool(
        accounts={"account": account},
        readiness={"account": _row()},
        states={},
        spam={},
        fingerprints={},
        hourly_counts={},
        daily_counts={},
        overrides={},
        limits=_limits(),
    )


def test_block_ladder_stops_at_cooldown_before_other_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _pool()._replace(accounts={}, readiness={})
    monkeypatch.setattr(_state, "in_cooldown", lambda *_a: True)

    assert (
        _gates._account_block_reason("account", "@channel", 1, datetime.now(UTC), pool)
        == "cooldown"
    )


def test_missing_account_reports_no_data_even_if_readiness_row_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Half the pair is on file and half is not, which is neither a readiness verdict nor a
    # gate: ``no_data`` is what the board badges a channel it has no rows for, and it says
    # the same thing here — nothing is known about this account, so nothing can be blamed.
    pool = _pool()._replace(accounts={})
    monkeypatch.setattr(_state, "in_cooldown", lambda *_a: False)

    assert (
        _gates._account_block_reason("account", "@channel", 1, datetime.now(UTC), pool) == "no_data"
    )


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (_row(banned=True, ready=True), "banned"),  # a stale ready=1 never beats the ban
        (_row(), None),  # joined, past the bot check, ready
        (_row(ready=False, captcha_passed=False), "chat_restricted"),
        (_row(ready=False, joined=False, captcha_passed=True), "rejoining"),
        (
            _row(ready=False, joined=False, captcha_passed=True, rejoin_gave_up=True),
            "rejoin_exhausted",
        ),
        (
            # ``_rejoin`` will not re-join a skipped pair, so its sentinel never
            # self-resolves — the terminal reading, exactly as the board badges it.
            _row(ready=False, joined=False, captcha_passed=True, human_skipped=True),
            "join_failed",
        ),
        (_row(ready=False, joined=False, captcha_passed=False), "join_by_request"),
        (_row(ready=False), "not_ready"),  # joined and past the bot check, still not ready
    ],
)
def test_pair_ladder_names_each_readiness_state(
    row: NeurocommentReadiness, expected: str | None
) -> None:
    """Every rung of the shared per-row ladder, which the board badges off too."""
    assert _pair_status.pair_block_reason(row) == expected


@pytest.mark.parametrize(
    ("reasons", "expected"),
    [
        ({"not_ready", "unhealthy"}, "unhealthy"),
        ({"cooldown", "quota_day"}, "quota_day"),
        ({"quota_hour", "quota_day"}, "quota_hour"),
        # Not a health verdict, so it must not be reported behind one: a quota label
        # sends the operator to raise a cap that was never the reason.
        ({"busy_neuroshilling", "quota_hour"}, "busy_neuroshilling"),
        # Terminal before transient at comparable distance: the mute announces itself the
        # moment the chat opens up, the permanent loss would otherwise never be reported.
        ({"banned", "chat_restricted"}, "banned"),
        # Distance first: ``not_handed_off`` is only reached once readiness said ready.
        ({"not_handed_off", "human_skipped"}, "not_handed_off"),
        ({"not_ready", "rejoin_exhausted"}, "rejoin_exhausted"),
    ],
)
def test_selection_miss_reports_highest_priority_blocker(
    monkeypatch: pytest.MonkeyPatch, reasons: set[str], expected: str
) -> None:
    accounts = sorted(reasons)
    monkeypatch.setattr(
        _gates,
        "_account_block_reason",
        lambda account_id, *_args: account_id,
    )

    assert (
        _gates._selection_block_reason(accounts, "@channel", 1, datetime.now(UTC), _pool())
        == expected
    )


# --------------------------------------------------------------------------- #
# Exclusion from a running neuroshilling campaign
# --------------------------------------------------------------------------- #


def test_a_neuroshilling_hold_blocks_before_every_other_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First in the ladder, on a pool that would otherwise say the account is perfect.

    The account is healthy, ready, uncooled and under both caps here, so nothing but the
    registry read can produce this verdict.
    """
    monkeypatch.setattr(_state, "in_cooldown", lambda *_a: False)
    _account_owner.try_claim("account", "neuroshilling", "ns-1")

    assert (
        _gates._account_block_reason("account", "@channel", 1, datetime.now(UTC), _pool())
        == "busy_neuroshilling"
    )


def test_a_warming_hold_is_not_the_selection_gates_business(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ``neuroshilling`` blocks here, not "somebody owns this account".

    Warming and neurocomment were already exclusive through promotion and hand-off, and
    those gates report far better reasons than a registry owner would. Widening this
    branch to any owner would silently take that vocabulary away — so the verdict here
    is the ladder's own (this pool carries no warming row), never ``busy_neuroshilling``.
    """
    monkeypatch.setattr(_state, "in_cooldown", lambda *_a: False)
    _account_owner.try_claim("account", "warming", "run-1")

    assert (
        _gates._account_block_reason("account", "@channel", 1, datetime.now(UTC), _pool())
        == "not_handed_off"
    )


@pytest.mark.asyncio
async def test_selection_skips_an_account_a_neuroshilling_run_holds() -> None:
    """Through the real ``_select_account``, on the only account the campaign has.

    Selection runs on EVERY incoming post — a start-time check would be blind to a
    campaign that took the account between two of them — so this is the gate that has to
    hold, and the miss reason is what the activity log shows the operator.
    """
    await _make_campaign("@chan", "acc-1")
    campaign = await fetch_active_campaign_for_channel("@chan")
    assert campaign is not None
    limits = await load_neuro_settings()
    assert (await engine._select_account(campaign, "@chan", limits)).account_id == "acc-1"

    _account_owner.try_claim("acc-1", "neuroshilling", "ns-1")

    selection = await engine._select_account(campaign, "@chan", limits)
    assert selection.account_id is None
    assert selection.reason == "busy_neuroshilling"
