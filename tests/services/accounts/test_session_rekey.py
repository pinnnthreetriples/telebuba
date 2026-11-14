"""Re-key policy: proxy and idle account first, the old key ended now or scheduled, never lost."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.repositories.account_rekeys import fetch_rekey, save_rekey
from core.telegram_client import RekeyGatewayError, SessionAuditGatewayError
from schemas.accounts import AccountRead
from schemas.session_audit import AccountSession, AuthorizationList
from schemas.session_close import SessionCloseOutcome
from schemas.session_rekey import RekeyResult, StoredRekey
from services import _account_owner
from services.accounts import AccountActionError, AccountNotFoundError
from services.accounts import session_rekey as svc

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


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
        self.listing: list[AccountSession] = []
        self.listing_failure: Exception | None = None

    async def read_sessions(
        self, _account_id: str, _devices: list[str], _own: dict[str, str] | None = None
    ) -> AuthorizationList:
        if self.listing_failure is not None:
            raise self.listing_failure
        return AuthorizationList(sessions=self.listing, ttl_days=180)


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

    async def _password(_account_id: str) -> list[str]:
        return [state.password] if state.password else []

    async def _rekey(
        account_id: str, device: str, scratch: str, passwords: list[str]
    ) -> RekeyResult:
        state.rekeys.append((account_id, device, scratch, passwords[0] if passwords else None))
        if state.rekey_failure is not None:
            raise state.rekey_failure
        return RekeyResult(old_hash="111", new_hash="222")

    async def _close(
        _account_id: str,
        _devices: list[str],
        hashes: list[str],
        _pauses: list[float],
        _own: dict[str, str] | None = None,
    ) -> list[SessionCloseOutcome]:
        state.closes.append(list(hashes))
        if state.close_failure is not None:
            raise state.close_failure
        return [] if state.close_outcome is None else [state.close_outcome]

    async def _audit(_account: AccountRead) -> None:
        state.audits += 1

    monkeypatch.setattr(svc, "require_account", _require)
    monkeypatch.setattr(svc, "fetch_account", _fetch_account)
    monkeypatch.setattr(svc, "twofa_password_candidates", _password)
    monkeypatch.setattr(svc, "rekey_session", _rekey)
    monkeypatch.setattr(svc, "close_sessions", _close)
    monkeypatch.setattr(svc, "read_account_sessions", state.read_sessions)
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


def _session(
    hash_: str,
    *,
    created: str,
    active: str,
    current: bool = False,
    ours: bool = False,
) -> AccountSession:
    return AccountSession(
        hash=hash_,
        is_current=current,
        is_ours=ours or current,
        official_app=False,
        device_model="Model",
        platform="Windows",
        system_version="10",
        app_name="Telegram Desktop",
        ip="192.0.2.10",
        country="Nowhere",
        date_created=created,
        date_active=active,
    )


def _legacy(account_id: str = "acc-1", *, rekeyed_at: str) -> StoredRekey:
    """A row the bug wrote: the asking session's own ``0`` as the old hash."""
    return StoredRekey(
        account_id=account_id,
        old_hash="0",
        rekeyed_at=rekeyed_at,
        close_after=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    )


def _account_a() -> list[AccountSession]:
    """Production account A (hash suffixes only): rekeyed 2026-11-07T13:34:45Z."""
    return [
        _session(
            "0",
            created="2026-11-07T13:34:38+00:00",
            active="2026-11-14T08:00:00+00:00",
            current=True,
        ),
        _session("6204", created="2026-11-02T10:00:00+00:00", active="2026-11-13T11:52:00+00:00"),
        _session("3706", created="2026-10-31T09:00:00+00:00", active="2026-11-07T11:00:00+00:00"),
        _session("4487", created="2026-11-02T18:28:00+00:00", active="2026-11-02T18:28:00+00:00"),
        _session("4807", created="2026-11-02T18:30:00+00:00", active="2026-11-02T18:30:00+00:00"),
        _session("5989", created="2026-11-02T18:32:00+00:00", active="2026-11-02T18:32:00+00:00"),
    ]


def _account_b() -> list[AccountSession]:
    """Production account B: rekeyed 2026-11-08T11:52:53Z."""
    return [
        _session(
            "0",
            created="2026-11-08T11:52:46+00:00",
            active="2026-11-14T08:00:00+00:00",
            current=True,
        ),
        _session("8435", created="2026-11-04T10:00:00+00:00", active="2026-11-13T10:00:00+00:00"),
        _session("8804", created="2026-09-02T10:00:00+00:00", active="2026-11-08T10:51:00+00:00"),
    ]


@pytest.mark.parametrize(
    ("sessions", "rekeyed_at", "expected"),
    [
        (_account_a, "2026-11-07T13:34:45+00:00", "3706"),
        (_account_b, "2026-11-08T11:52:53+00:00", "8804"),
    ],
)
@pytest.mark.asyncio
async def test_a_row_stored_with_zero_is_resolved_to_the_one_session_the_approver_used(
    wire: _Wire, sessions: Callable[[], list[AccountSession]], rekeyed_at: str, expected: str
) -> None:
    wire.listing = sessions()
    wire.close_outcome = SessionCloseOutcome(hash=expected, status="closed")
    await save_rekey(_legacy(rekeyed_at=rekeyed_at))

    ended = await svc.close_due_old_keys()

    assert ended == 1
    assert wire.closes == [[expected]]  # never "0": resolved first, then closed in the same call
    stored = await fetch_rekey("acc-1")
    assert stored is not None
    assert (stored.old_hash, stored.problem) == (expected, None)
    assert stored.closed_at is not None


@pytest.mark.parametrize(
    "extra",
    [
        [],  # nothing was active around the re-key
        [("9999", "2026-10-01T00:00:00+00:00", "2026-11-07T12:00:00+00:00")],  # a second one
    ],
)
@pytest.mark.asyncio
async def test_a_zero_row_without_exactly_one_candidate_stops_for_the_operator(
    wire: _Wire, extra: list[tuple[str, str, str]]
) -> None:
    wire.listing = [
        _session(
            "0",
            created="2026-11-07T13:34:38+00:00",
            active="2026-11-14T08:00:00+00:00",
            current=True,
        ),
        _session("6204", created="2026-11-02T10:00:00+00:00", active="2026-11-13T11:52:00+00:00"),
        *(_session(h, created=c, active=a) for h, c, a in extra),
    ]
    if extra:
        wire.listing.append(
            _session(
                "3706", created="2026-10-31T09:00:00+00:00", active="2026-11-07T11:00:00+00:00"
            )
        )
    await save_rekey(_legacy(rekeyed_at="2026-11-07T13:34:45+00:00"))

    ended = await svc.close_due_old_keys()

    assert ended == 0
    assert wire.closes == []
    stored = await fetch_rekey("acc-1")
    assert stored is not None
    assert (stored.problem, stored.close_after, stored.closed_at) == ("old_key_unknown", None, None)
    # Terminal: the background pass no longer picks it up. The old key was never identifiable,
    # so a new re-key is allowed and replaces the row (the operator closes it by hand).
    assert await svc.close_due_old_keys() == 0
    view = await svc.rekey_account_session("acc-1")
    assert view.problem is None
    assert len(wire.rekeys) == 1


@pytest.mark.asyncio
async def test_a_zero_row_whose_listing_cannot_be_read_is_retried_after_the_flood_wait(
    wire: _Wire,
) -> None:
    wire.listing_failure = SessionAuditGatewayError("flood_wait", retry_after_seconds=7200)
    await save_rekey(_legacy(rekeyed_at="2026-11-07T13:34:45+00:00"))

    await svc.close_due_old_keys()

    stored = await fetch_rekey("acc-1")
    assert stored is not None
    assert (stored.old_hash, stored.problem) == ("0", None)
    due = datetime.fromisoformat(stored.close_after or "") - datetime.now(UTC)
    assert timedelta(seconds=7200) < due < timedelta(seconds=7200 + 120)


@pytest.mark.asyncio
async def test_a_flood_wait_before_the_close_is_honoured_not_the_default_hour(
    wire: _Wire,
) -> None:
    wire.close_failure = SessionAuditGatewayError("flood_wait", retry_after_seconds=10_000)

    view = await svc.rekey_account_session("acc-1")

    due = datetime.fromisoformat(view.close_after or "") - datetime.now(UTC)
    assert timedelta(seconds=10_000) < due < timedelta(seconds=10_000 + 120)


@pytest.mark.asyncio
async def test_a_real_hash_that_turns_out_to_be_ours_is_a_terminal_problem_not_a_retry(
    wire: _Wire,
) -> None:
    wire.close_outcome = SessionCloseOutcome(hash="111", status="not_foreign")

    view = await svc.rekey_account_session("acc-1")

    assert (view.problem, view.close_after, view.closed_at) == ("old_key_is_ours", None, None)
    assert wire.closes == [["111"]]
    assert await svc.close_due_old_keys() == 0
    assert wire.closes == [["111"]]  # never tried again


@pytest.mark.asyncio
async def test_a_pending_close_logs_why(wire: _Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[tuple[str, dict[str, object]]] = []

    async def _log(
        _level: str,
        event: str,
        *,
        account_id: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        assert account_id == "acc-1"
        logged.append((event, extra or {}))

    monkeypatch.setattr(svc, "log_event", _log)
    wire.close_outcome = SessionCloseOutcome(hash="111", status="failed", error_type="RPCError")

    await svc.rekey_account_session("acc-1")

    assert ("session_old_key_pending", {"status": "failed", "error_type": "RPCError"}) in logged


@pytest.mark.asyncio
async def test_the_view_carries_the_problem(wire: _Wire) -> None:
    del wire
    await save_rekey(
        StoredRekey(
            account_id="acc-1",
            old_hash="5",
            rekeyed_at="2026-11-01T00:00:00+00:00",
            problem="old_key_unknown",
        )
    )

    view = await svc.read_account_rekey("acc-1")

    assert view.problem == "old_key_unknown"


@pytest.mark.asyncio
async def test_an_old_session_found_to_be_ours_blocks_a_new_rekey(wire: _Wire) -> None:
    wire.close_outcome = SessionCloseOutcome(hash="111", status="not_foreign")
    await svc.rekey_account_session("acc-1")
    wire.rekeys.clear()

    with pytest.raises(AccountActionError) as refused:
        await svc.rekey_account_session("acc-1")

    assert refused.value.code == "rekey_needs_attention"
    assert wire.rekeys == []


@pytest.mark.asyncio
async def test_an_unreachable_close_logs_one_pending_event_with_everything(
    wire: _Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    logged: list[tuple[str, dict[str, object]]] = []

    async def _log(
        _level: str,
        event: str,
        *,
        account_id: str | None = None,  # noqa: ARG001
        extra: dict[str, object] | None = None,
    ) -> None:
        logged.append((event, extra or {}))

    monkeypatch.setattr(svc, "log_event", _log)
    wire.close_failure = SessionAuditGatewayError("unavailable", error_type="TimeoutError")

    await svc.rekey_account_session("acc-1")

    pending = [extra for event, extra in logged if event == "session_old_key_pending"]
    assert pending == [
        {"status": "unreachable", "code": "unavailable", "error_type": "TimeoutError"}
    ]
