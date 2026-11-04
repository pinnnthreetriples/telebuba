"""Re-key policy: proxy and idle account first, the old key ended now or scheduled, never lost."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.repositories.account_rekeys import fetch_rekey, save_rekey
from core.telegram_client import RekeyGatewayError, SessionAuditGatewayError
from schemas.accounts import AccountRead
from schemas.session_close import SessionCloseOutcome
from schemas.session_rekey import RekeyResult, StoredRekey
from services import _account_owner
from services.accounts import AccountActionError, AccountNotFoundError
from services.accounts import session_rekey as svc

if TYPE_CHECKING:
    from collections.abc import Iterator


def _account(**fields: object) -> AccountRead:
    values: dict[str, object] = {
        "account_id": "acc-1",
        "session_name": "sess-1",
        "status": "alive",
        "created_at": "2026-10-31T00:00:00+00:00",
        "updated_at": "2026-10-31T00:00:00+00:00",
        "proxy_id": "proxy-1",
        "devices": ["mobile", "desktop"],
    }
    return AccountRead.model_validate(values | fields)


class _Wire:
    def __init__(self) -> None:
        self.account: AccountRead | None = _account()
        self.password: str | None = "pw-stored"
        self.rekeys: list[tuple[str, str, str, str | None]] = []
        self.rekey_failure: Exception | None = None
        self.close_outcome: SessionCloseOutcome | None = SessionCloseOutcome(
            hash="111", status="closed"
        )
        self.close_failure: Exception | None = None
        self.closes: list[list[str]] = []
        self.audits = 0


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Wire]:
    state = _Wire()
    _account_owner.reset_for_tests()

    async def _require(account_id: str) -> AccountRead:
        if state.account is None:
            raise AccountNotFoundError(account_id)
        return state.account

    async def _fetch_account(_account_id: str) -> AccountRead | None:
        return state.account

    async def _password(_account_id: str) -> str | None:
        return state.password

    async def _rekey(
        account_id: str, device: str, scratch: str, password: str | None
    ) -> RekeyResult:
        state.rekeys.append((account_id, device, scratch, password))
        if state.rekey_failure is not None:
            raise state.rekey_failure
        return RekeyResult(old_hash="111", new_hash="222")

    async def _close(
        _account_id: str, _devices: list[str], hashes: list[str], _pauses: list[float]
    ) -> list[SessionCloseOutcome]:
        state.closes.append(list(hashes))
        if state.close_failure is not None:
            raise state.close_failure
        return [] if state.close_outcome is None else [state.close_outcome]

    async def _audit(_account: AccountRead) -> None:
        state.audits += 1

    monkeypatch.setattr(svc, "require_account", _require)
    monkeypatch.setattr(svc, "fetch_account", _fetch_account)
    monkeypatch.setattr(svc, "fetch_account_twofa_password", _password)
    monkeypatch.setattr(svc, "rekey_session", _rekey)
    monkeypatch.setattr(svc, "close_sessions", _close)
    monkeypatch.setattr(svc, "refresh_account_audit", _audit)
    yield state
    # A claim a test made must not outlive it: the registry is process-wide.
    _account_owner.reset_for_tests()


@pytest.mark.asyncio
async def test_a_rekey_swaps_the_primary_session_and_ends_the_old_key_at_once(wire: _Wire) -> None:
    view = await svc.rekey_account_session("acc-1")

    # The primary device, a scratch name beside the account's own, the stored password.
    assert wire.rekeys == [("acc-1", "mobile", "sess-1_rekey", "pw-stored")]
    assert wire.closes == [["111"]]
    assert view.rekeyed_at is not None
    assert (view.closed_at is not None, view.close_after) == (True, None)
    stored = await fetch_rekey("acc-1")
    assert stored is not None
    assert (stored.old_hash, stored.close_after) == ("111", None)
    assert wire.audits == 1


@pytest.mark.asyncio
async def test_a_refusal_for_being_too_young_schedules_the_close_and_keeps_the_key_to_end(
    wire: _Wire,
) -> None:
    wire.close_outcome = SessionCloseOutcome(hash="111", status="too_new", retry_after_seconds=7200)

    view = await svc.rekey_account_session("acc-1")

    assert view.closed_at is None
    assert view.close_after is not None
    due = datetime.fromisoformat(view.close_after) - datetime.now(UTC)
    # The wait Telegram's rule leaves, plus a short margin.
    assert timedelta(seconds=7200) < due < timedelta(seconds=7200 + 120)
    assert (await fetch_rekey("acc-1")).closed_at is None  # ty: ignore[unresolved-attribute]


@pytest.mark.asyncio
async def test_a_close_that_could_not_reach_telegram_is_retried_after_the_configured_wait(
    wire: _Wire,
) -> None:
    wire.close_failure = SessionAuditGatewayError("unavailable")

    view = await svc.rekey_account_session("acc-1")

    assert view.closed_at is None
    retry = settings_retry()
    due = datetime.fromisoformat(view.close_after or "") - datetime.now(UTC)
    assert timedelta(seconds=retry - 5) < due <= timedelta(seconds=retry)


def settings_retry() -> float:
    return svc.settings.session_audit.rekey_retry_seconds


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (RekeyGatewayError("rekey_twofa_missing"), "rekey_twofa_missing"),
        (RekeyGatewayError("flood_wait", retry_after_seconds=30), "flood_wait"),
    ],
)
@pytest.mark.asyncio
async def test_a_gateway_refusal_is_its_code_and_nothing_is_stored(
    wire: _Wire, failure: Exception, code: str
) -> None:
    wire.rekey_failure = failure

    with pytest.raises(AccountActionError) as refused:
        await svc.rekey_account_session("acc-1")

    assert refused.value.code == code
    assert await fetch_rekey("acc-1") is None
    assert wire.closes == []


@pytest.mark.asyncio
async def test_an_account_without_a_proxy_is_refused_before_anything_happens(wire: _Wire) -> None:
    wire.account = _account(proxy_id=None)

    with pytest.raises(AccountActionError) as refused:
        await svc.rekey_account_session("acc-1")

    assert refused.value.code == "rekey_proxy_missing"
    assert wire.rekeys == []


@pytest.mark.asyncio
async def test_an_account_another_feature_holds_is_not_swapped_under_it(wire: _Wire) -> None:
    _account_owner.try_claim("acc-1", "warming", "run-1")

    with pytest.raises(AccountActionError) as refused:
        await svc.rekey_account_session("acc-1")

    assert refused.value.code == "rekey_account_busy"
    assert wire.rekeys == []


@pytest.mark.asyncio
async def test_a_second_rekey_waits_for_the_first_ones_old_key(wire: _Wire) -> None:
    await save_rekey(
        StoredRekey(
            account_id="acc-1",
            old_hash="5",
            rekeyed_at="2026-11-01T00:00:00+00:00",
            close_after="2026-11-02T00:00:00+00:00",
        )
    )

    with pytest.raises(AccountActionError) as refused:
        await svc.rekey_account_session("acc-1")

    assert refused.value.code == "rekey_pending"
    assert wire.rekeys == []


@pytest.mark.asyncio
async def test_after_the_old_key_is_ended_the_account_may_be_re_keyed_again(wire: _Wire) -> None:
    await save_rekey(
        StoredRekey(
            account_id="acc-1",
            old_hash="5",
            rekeyed_at="2026-11-01T00:00:00+00:00",
            closed_at="2026-11-01T01:00:00+00:00",
        )
    )

    await svc.rekey_account_session("acc-1")

    assert len(wire.rekeys) == 1


@pytest.mark.usefixtures("wire")
@pytest.mark.asyncio
async def test_the_view_says_never_re_keyed_before_the_first() -> None:
    view = await svc.read_account_rekey("acc-1")

    assert (view.rekeyed_at, view.closed_at, view.close_after) == (None, None, None)


@pytest.mark.asyncio
async def test_an_unknown_account_is_not_found(wire: _Wire) -> None:
    wire.account = None

    with pytest.raises(AccountNotFoundError):
        await svc.rekey_account_session("acc-1")


def _due(account_id: str, *, after: timedelta) -> StoredRekey:
    return StoredRekey(
        account_id=account_id,
        old_hash="111",
        rekeyed_at="2026-11-01T00:00:00+00:00",
        close_after=(datetime.now(UTC) + after).isoformat(),
    )


@pytest.mark.asyncio
async def test_the_background_pass_ends_only_the_old_keys_whose_time_has_come(wire: _Wire) -> None:
    await save_rekey(_due("acc-1", after=-timedelta(minutes=1)))
    await save_rekey(_due("acc-2", after=timedelta(hours=3)))

    ended = await svc.close_due_old_keys()

    assert ended == 1
    assert wire.closes == [["111"]]
    assert (await fetch_rekey("acc-1")).closed_at is not None  # ty: ignore[unresolved-attribute]
    assert (await fetch_rekey("acc-2")).closed_at is None  # ty: ignore[unresolved-attribute]
    assert wire.audits == 1


@pytest.mark.asyncio
async def test_the_background_pass_reschedules_a_key_telegram_still_refuses(wire: _Wire) -> None:
    wire.close_outcome = SessionCloseOutcome(hash="111", status="too_new", retry_after_seconds=3600)
    await save_rekey(_due("acc-1", after=-timedelta(minutes=1)))

    ended = await svc.close_due_old_keys()

    assert ended == 0
    rekey = await fetch_rekey("acc-1")
    assert rekey is not None
    assert rekey.closed_at is None
    assert datetime.fromisoformat(rekey.close_after or "") > datetime.now(UTC) + timedelta(
        minutes=59
    )


@pytest.mark.asyncio
async def test_a_deleted_account_is_skipped_by_the_background_pass(wire: _Wire) -> None:
    await save_rekey(_due("acc-1", after=-timedelta(minutes=1)))
    wire.account = None

    assert await svc.close_due_old_keys() == 0
    assert wire.closes == []
