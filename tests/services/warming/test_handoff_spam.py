"""The hand-off to neurocomment needs a ``clean`` @SpamBot answer (``_handoff_spam``)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.db import (
    create_account,
    fetch_warming_state,
    mark_promoted_to_nc,
    upsert_spam_status,
    upsert_warming_state,
)
from schemas.accounts import AccountCreate
from schemas.spam_status import SpamStatusKind, SpamStatusVerdict
from schemas.warming import WarmingStateWrite
from services import _account_owner, spam_status, warming
from services.warming import _runtime, _seams
from tests.services.warming._support import _fake_loop


class _Probe:
    def __init__(self, status: SpamStatusKind) -> None:
        self.status: SpamStatusKind = status
        self.forces: list[bool] = []

    async def __call__(self, account_id: str, *, force: bool = False) -> SpamStatusVerdict:
        self.forces.append(force)
        return SpamStatusVerdict(
            account_id=account_id,
            status=self.status,
            checked_at=datetime.now(UTC).isoformat(),
        )


def _probe(monkeypatch: pytest.MonkeyPatch, status: SpamStatusKind) -> _Probe:
    probe = _Probe(status)
    monkeypatch.setattr(_seams, "_refresh_spam_status", probe)
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    return probe


async def _graduated(account_id: str = "acc-1") -> None:
    await create_account(AccountCreate(account_id=account_id))
    started = (datetime.now(UTC) - timedelta(days=20)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(account_id=account_id, state="idle", started_at=started),
    )
    await mark_promoted_to_nc(account_id)


@pytest.fixture(autouse=True)
def _release_owners() -> object:
    _account_owner.reset_for_tests()
    yield
    for task in list(warming._RUNTIME.values()):
        task.cancel()
    _account_owner.reset_for_tests()


@pytest.mark.asyncio
async def test_a_fresh_clean_reading_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    # The real cache in front of a probe that would answer "limited": only a served
    # cache hit can hand the account off.
    probe = _Probe("limited")
    monkeypatch.setattr(_seams, "_refresh_spam_status", spam_status.refresh_spam_status)
    monkeypatch.setattr(spam_status, "check_spam_status", probe)
    await _graduated()
    checked = (datetime.now(UTC) - timedelta(hours=10)).isoformat()
    await upsert_spam_status(
        SpamStatusVerdict(account_id="acc-1", status="clean", checked_at=checked),
    )

    card = await warming.handoff_to_neurocomment("acc-1")

    assert card.nc_handed_off is True
    assert probe.forces == [], "a reading inside the cache TTL must not ask again"


@pytest.mark.asyncio
async def test_no_reading_asks_and_hands_off_on_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = _probe(monkeypatch, "clean")
    await _graduated()

    card = await warming.handoff_to_neurocomment("acc-1")

    assert card.nc_handed_off is True
    assert probe.forces == [False]


@pytest.mark.asyncio
async def test_no_answer_refuses_the_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    _probe(monkeypatch, "unknown")
    await _graduated()

    with pytest.raises(warming.HandoffSpamRefusedError) as refused:
        await warming.handoff_to_neurocomment("acc-1")

    assert refused.value.code == "spam_check_unavailable"
    stored = await fetch_warming_state("acc-1")
    assert stored is not None
    assert stored.promoted_to_nc is True
    assert stored.nc_handed_off is False
    assert stored.state == "idle"


@pytest.mark.asyncio
async def test_a_cached_unknown_is_asked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = _probe(monkeypatch, "clean")
    await _graduated()
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id="acc-1", status="unknown", checked_at=datetime.now(UTC).isoformat()
        ),
    )

    card = await warming.handoff_to_neurocomment("acc-1")

    assert card.nc_handed_off is True
    assert probe.forces == [True]


@pytest.mark.asyncio
async def test_limited_returns_the_account_to_quarantine_with_a_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _probe(monkeypatch, "limited")
    await _graduated()

    with pytest.raises(warming.HandoffSpamRefusedError) as refused:
        await warming.handoff_to_neurocomment("acc-1")

    assert refused.value.code == "spam_limited"
    stored = await fetch_warming_state("acc-1")
    assert stored is not None
    assert stored.state == "quarantine"
    assert stored.last_event == "handoff_spam_limited"
    assert stored.promoted_to_nc is False
    assert stored.nc_handed_off is False
    assert stored.run_id is not None
    assert "acc-1" in warming._RUNTIME
    assert _account_owner.owner_of("acc-1") == "warming"


@pytest.mark.asyncio
async def test_limited_while_another_feature_holds_the_account_stores_state_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _probe(monkeypatch, "limited")
    await _graduated()
    _account_owner.take_over("acc-1", "neuroshilling", "campaign-1")

    with pytest.raises(warming.HandoffSpamRefusedError):
        await warming.handoff_to_neurocomment("acc-1")

    stored = await fetch_warming_state("acc-1")
    assert stored is not None
    assert stored.state == "quarantine"
    assert stored.run_id is None
    assert "acc-1" not in warming._RUNTIME
    assert _account_owner.owner_of("acc-1") == "neuroshilling"


@pytest.mark.asyncio
async def test_an_ungraduated_account_is_refused_without_asking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _probe(monkeypatch, "clean")
    await create_account(AccountCreate(account_id="raw"))

    with pytest.raises(ValueError, match="not in the warmed pool"):
        await warming.handoff_to_neurocomment("raw")
    assert probe.forces == []
