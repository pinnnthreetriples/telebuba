"""Neurocomment's @SpamBot callers name themselves; onboarding paces on real probes only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from schemas.spam_status import SpamStatusVerdict
from services.neurocomment import _seams, bans, onboarding
from services.spam_intel import _gateway

if TYPE_CHECKING:
    from schemas.spam_intel import ServedFrom

_START = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


class _Asker:
    """Stands in for the neurocomment seam; records the caller scope and the force flag."""

    def __init__(self, status: str = "limited") -> None:
        self.status = status
        self.seen: list[tuple[str | None, bool]] = []

    async def __call__(self, account_id: str, *, force: bool = False) -> SpamStatusVerdict:
        self.seen.append((_gateway._CALLER.get(), force))
        return SpamStatusVerdict(
            account_id=account_id,
            status=self.status,  # ty: ignore[invalid-argument-type]
            checked_at=_START.isoformat(),
        )


@pytest.mark.asyncio
async def test_the_ban_confirmation_asks_forced_in_its_own_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asker = _Asker()
    monkeypatch.setattr(_seams, "refresh_spam_status", asker)
    assert (
        await bans.confirm_group_ban_and_leave("acc-1", "@chan", known_state="restricted") is False
    )
    assert asker.seen == [("neurocomment.ban_confirm", True)]


@pytest.mark.asyncio
async def test_the_confirmation_asks_nothing_when_the_group_record_is_not_restricted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asker = _Asker()
    monkeypatch.setattr(_seams, "refresh_spam_status", asker)
    assert await bans.confirm_group_ban_and_leave("acc-1", "@chan", known_state="can_send") is False
    assert asker.seen == []


@pytest.mark.asyncio
async def test_the_irreversible_step_asks_forced_under_the_budget_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asker = _Asker()
    monkeypatch.setattr(_seams, "refresh_spam_status", asker)
    spent = await bans._ban_on_a_spent_budget(
        "acc-1", "@chan", known_state="can_send", failures=2, rounds=2
    )
    assert spent is None  # a limited account keeps its position
    assert asker.seen == [("neurocomment.ban_budget", True)]


@pytest.mark.parametrize(
    ("served_from", "seconds_ago", "expected"),
    [
        ("probe", 3600, True),  # sent: whatever the stamp says
        ("cache", 0, False),  # a cache hit stamped "now" sent nothing
        ("coalesced", 0, False),
        ("deferred", 0, False),
        ("refused", 0, False),
        (None, -5, True),  # a verdict without the field: the old timestamp rule
        (None, 3600, False),
    ],
)
def test_onboarding_pauses_only_after_a_real_probe(
    served_from: ServedFrom | None,
    seconds_ago: int,
    expected: object,
) -> None:
    run_start = _START
    checked = (
        run_start - timedelta(seconds=seconds_ago)
        if seconds_ago >= 0
        else run_start + timedelta(seconds=5)
    )
    verdict = SpamStatusVerdict(
        account_id="acc-1",
        status="clean",
        checked_at=checked.isoformat(),
        served_from=served_from,
    )
    assert onboarding._probed_this_run(verdict, run_start) is expected


def test_a_naive_legacy_stamp_is_read_as_utc() -> None:
    verdict = SpamStatusVerdict(
        account_id="acc-1", status="clean", checked_at="2026-11-16T12:00:05"
    )
    assert onboarding._probed_this_run(verdict, _START) is True
    broken = SpamStatusVerdict(account_id="acc-1", status="clean", checked_at="garbage")
    assert onboarding._probed_this_run(broken, _START) is False


@pytest.mark.asyncio
async def test_onboarding_asks_in_its_own_name(monkeypatch: pytest.MonkeyPatch) -> None:
    asker = _Asker("clean")
    monkeypatch.setattr(_seams, "refresh_spam_status", asker)
    await onboarding._probe_account_spam(["acc-1"])
    assert asker.seen == [("neurocomment.onboarding", False)]
