"""The old key is the operator's to end: no automatic close, a checklist, a reminder.

``SESSION_AUDIT__AUTO_CLOSE_OLD_KEY`` is off by default. An account's own key once died about
a second after Telebuba ended its old official session from a six-day-old unofficial one, so
the re-key keeps the old authorization alive until the operator closes it from the card.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.repositories.account_rekeys import fetch_rekey, save_rekey
from core.telegram_client import RekeyGatewayError
from schemas.accounts import AccountRead
from schemas.lifecycle import LifecycleEvent
from schemas.session_close import SessionCloseOutcome
from schemas.session_rekey import RekeyResult, StoredRekey
from schemas.spam_status import SpamStatusVerdict
from services import _account_owner
from services.accounts import AccountActionError
from services.accounts import _old_key_checklist as checklist
from services.accounts import session_rekey as svc

if TYPE_CHECKING:
    from collections.abc import Iterator

_NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def _account(**fields: object) -> AccountRead:
    values: dict[str, object] = {
        "account_id": "acc-1",
        "session_name": "sess-1",
        "status": "alive",
        "created_at": "2026-10-31T00:00:00+00:00",
        "updated_at": "2026-10-31T00:00:00+00:00",
        "proxy_id": "proxy-1",
        "devices": ["desktop"],
    }
    return AccountRead.model_validate(values | fields)


class _Wire:
    def __init__(self) -> None:
        self.account = _account()
        self.closes: list[list[str]] = []
        self.close_outcome = SessionCloseOutcome(hash="111", status="closed")
        self.rekey_failure: Exception | None = None
        self.audits = 0
        self.logged: list[tuple[str, str | None, dict[str, object]]] = []


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Wire]:
    state = _Wire()
    _account_owner.reset_for_tests()
    svc.reset_for_tests()

    async def _require(_account_id: str) -> AccountRead:
        return state.account

    async def _fetch_account(_account_id: str) -> AccountRead | None:
        return state.account

    async def _passwords(_account_id: str) -> list[str]:
        return ["pw"]

    async def _rekey(*_args: object) -> RekeyResult:
        if state.rekey_failure is not None:
            raise state.rekey_failure
        return RekeyResult(old_hash="111", new_hash="222")

    async def _close(
        _account_id: str, _devices: object, hashes: list[str], *_rest: object
    ) -> list[SessionCloseOutcome]:
        state.closes.append(list(hashes))
        return [state.close_outcome]

    async def _audit(_account: AccountRead) -> None:
        state.audits += 1

    async def _log(
        _level: str,
        event: str,
        *,
        account_id: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        state.logged.append((event, account_id, extra or {}))

    monkeypatch.setattr(svc.settings.session_audit, "auto_close_old_key", False)
    monkeypatch.setattr(svc, "require_account", _require)
    monkeypatch.setattr(svc, "require_proxied_account", _require)
    monkeypatch.setattr(svc, "fetch_account", _fetch_account)
    monkeypatch.setattr(svc, "twofa_password_candidates", _passwords)
    monkeypatch.setattr(svc, "rekey_session", _rekey)
    monkeypatch.setattr(svc, "close_sessions", _close)
    monkeypatch.setattr(svc, "refresh_account_audit", _audit)
    monkeypatch.setattr(svc, "log_event", _log)
    yield state
    _account_owner.reset_for_tests()
    svc.reset_for_tests()


def _events(wire: _Wire, name: str) -> list[tuple[str, str | None, dict[str, object]]]:
    return [entry for entry in wire.logged if entry[0] == name]


@pytest.mark.asyncio
async def test_a_rekey_leaves_the_old_key_alive_for_the_operator(wire: _Wire) -> None:
    view = await svc.rekey_account_session("acc-1")

    assert wire.closes == []
    stored = await fetch_rekey("acc-1")
    assert stored is not None
    assert (stored.old_hash, stored.close_after, stored.closed_at) == ("111", None, None)
    assert view.auto_close is False
    assert [check.name for check in view.checklist] == [
        "spam_clean",
        "session_age",
        "fallback_session",
        "twofa_stored",
        "passkey_stored",
    ]


@pytest.mark.asyncio
async def test_the_background_pass_ends_nothing_and_reminds_once_a_day(wire: _Wire) -> None:
    await save_rekey(
        StoredRekey(
            account_id="acc-1",
            old_hash="111",
            rekeyed_at="2026-11-08T11:52:53+00:00",
            close_after="2026-11-08T12:00:00+00:00",  # due, had the setting been on
        )
    )

    assert await svc.close_due_old_keys() == 0
    assert await svc.close_due_old_keys() == 0

    assert wire.closes == []
    assert _events(wire, "session_old_key_open") == [
        ("session_old_key_open", "acc-1", {"rekeyed_at": "2026-11-08T11:52:53+00:00"})
    ]


@pytest.mark.asyncio
async def test_the_operator_closes_the_old_key_from_the_card(wire: _Wire) -> None:
    await svc.rekey_account_session("acc-1")
    audits_before = wire.audits

    view = await svc.close_account_old_key("acc-1")

    assert wire.closes == [["111"]]
    assert view.closed_at is not None
    assert view.checklist == []
    assert wire.audits == audits_before + 1
    assert _events(wire, "session_old_key_close_requested")


@pytest.mark.asyncio
async def test_a_close_telegram_refuses_says_when_to_try_and_nothing_tries_it(wire: _Wire) -> None:
    await svc.rekey_account_session("acc-1")
    wire.close_outcome = SessionCloseOutcome(hash="111", status="too_new", retry_after_seconds=600)

    view = await svc.close_account_old_key("acc-1")

    assert view.closed_at is None
    assert view.close_after is not None
    assert await svc.close_due_old_keys() == 0
    assert wire.closes == [["111"]]


@pytest.mark.parametrize(
    ("row", "code"),
    [
        (None, "rekey_old_key_none"),
        (
            StoredRekey(
                account_id="acc-1",
                old_hash="111",
                rekeyed_at="2026-11-08T11:52:53+00:00",
                closed_at="2026-11-14T21:24:22+00:00",
            ),
            "rekey_old_key_none",
        ),
        (
            StoredRekey(
                account_id="acc-1",
                old_hash="111",
                rekeyed_at="2026-11-08T11:52:53+00:00",
                problem="rekey_incomplete",
            ),
            "rekey_needs_attention",
        ),
    ],
)
@pytest.mark.asyncio
async def test_closing_is_refused_when_there_is_nothing_safe_to_close(
    wire: _Wire, row: StoredRekey | None, code: str
) -> None:
    if row is not None:
        await save_rekey(row)

    with pytest.raises(AccountActionError) as refused:
        await svc.close_account_old_key("acc-1")

    assert refused.value.code == code
    assert wire.closes == []


@pytest.mark.asyncio
async def test_a_swap_that_could_not_be_put_back_is_kept_for_a_person(wire: _Wire) -> None:
    failure = RekeyGatewayError("rekey_incomplete", error_type="OperationalError")
    failure.old_hash = "111"
    wire.rekey_failure = failure

    with pytest.raises(AccountActionError) as refused:
        await svc.rekey_account_session("acc-1")

    assert refused.value.code == "rekey_incomplete"
    stored = await fetch_rekey("acc-1")
    assert stored is not None
    assert (stored.old_hash, stored.problem, stored.close_after) == (
        "111",
        "rekey_incomplete",
        None,
    )
    assert _events(wire, "session_old_key_problem")
    # The old key is never tried, and no new re-key starts over it.
    assert await svc.close_due_old_keys() == 0
    wire.rekey_failure = None
    with pytest.raises(AccountActionError) as again:
        await svc.rekey_account_session("acc-1")
    assert again.value.code == "rekey_needs_attention"
    assert wire.closes == []


# --- the checklist -------------------------------------------------------------------


@pytest.fixture
def facts(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    state: dict[str, object] = {
        "spam": "clean",
        "verdicts": [],
        "passwords": ["pw"],
        "passkey": object(),
    }

    async def _spam(account_id: str) -> SpamStatusVerdict | None:
        status = state["spam"]
        if status is None:
            return None
        return SpamStatusVerdict(account_id=account_id, status=status, checked_at=_NOW.isoformat())  # ty: ignore[invalid-argument-type]

    async def _events_of(_account_id: str) -> object:
        return state["verdicts"]

    async def _passwords(_account_id: str) -> object:
        return state["passwords"]

    async def _passkey(_account_id: str) -> object:
        return state["passkey"]

    monkeypatch.setattr(checklist, "get_spam_status", _spam)
    monkeypatch.setattr(checklist, "list_lifecycle_events", _events_of)
    monkeypatch.setattr(checklist, "twofa_password_candidates", _passwords)
    monkeypatch.setattr(checklist, "fetch_account_passkey", _passkey)
    return state


def _verdict(status: str, days_ago: float) -> LifecycleEvent:
    return LifecycleEvent(
        id=1,
        account_id="acc-1",
        kind="spambot_verdict",
        created_at=(_NOW - timedelta(days=days_ago)).isoformat(),
        extra={"status": status},
    )


def _rekeyed(days_ago: float) -> StoredRekey:
    return StoredRekey(
        account_id="acc-1",
        old_hash="111",
        rekeyed_at=(_NOW - timedelta(days=days_ago)).isoformat(),
    )


async def _results(account: AccountRead, rekey: StoredRekey) -> dict[str, bool]:
    items = await checklist.old_key_checklist(account, rekey, now=_NOW)
    return {item.name: item.ok for item in items}


@pytest.mark.asyncio
async def test_an_account_like_the_one_that_died_fails_the_checklist(
    facts: dict[str, object],
) -> None:
    # Limited by SpamBot, re-keyed six days ago, no second sign-in of ours.
    facts["spam"] = "limited"
    facts["verdicts"] = [_verdict("limited", 13)]

    result = await _results(_account(), _rekeyed(6))

    assert result == {
        "spam_clean": False,
        "session_age": False,
        "fallback_session": False,
        "twofa_stored": True,
        "passkey_stored": True,
    }


@pytest.mark.asyncio
async def test_a_clean_settled_account_with_a_fallback_passes(facts: dict[str, object]) -> None:
    facts["verdicts"] = [_verdict("limited", 45), _verdict("clean", 3)]  # the limit is old news

    result = await _results(_account(second_session_name="sess-1_mobile"), _rekeyed(8))

    assert all(result.values()), result


@pytest.mark.asyncio
async def test_a_limit_within_the_window_fails_spam_clean_even_if_clean_now(
    facts: dict[str, object],
) -> None:
    facts["verdicts"] = [_verdict("limited", 10), _verdict("clean", 1)]
    facts["passwords"] = []
    facts["passkey"] = None

    result = await _results(_account(), _rekeyed(8))

    assert result == {
        "spam_clean": False,
        "session_age": True,
        "fallback_session": False,
        "twofa_stored": False,
        "passkey_stored": False,
    }


@pytest.mark.asyncio
async def test_no_spambot_answer_at_all_is_not_clean(facts: dict[str, object]) -> None:
    facts["spam"] = None

    result = await _results(_account(), _rekeyed(8))

    assert result["spam_clean"] is False
