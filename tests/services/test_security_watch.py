"""The security watch: a pending reset and changed settings are journalled, our own changes not."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import configure_database
from core.repositories.account_security_snapshots import (
    fetch_security_snapshot,
    forget_security_snapshot,
)
from schemas.telegram_actions_twofa import TwoFactorStatusResult
from schemas.twofa import AccountTwoFactorView
from services.accounts import AccountActionError
from services.session_guard import _security_watch

if TYPE_CHECKING:
    from pathlib import Path


class _Wire:
    def __init__(self) -> None:
        self.status: TwoFactorStatusResult | None = TwoFactorStatusResult()
        self.error: BaseException | None = None
        self.events: list[tuple[str, str, dict[str, object]]] = []


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Wire:
    configure_database(tmp_path / "telebuba.db")
    state = _Wire()

    async def _read(_account_id: str) -> AccountTwoFactorView:
        if state.error is not None:
            raise state.error
        return AccountTwoFactorView(status=state.status)

    async def _log(level: str, event: str, **kwargs: object) -> None:
        extra: dict[str, object] = dict(kwargs.get("extra") or {})  # ty: ignore[no-matching-overload]
        state.events.append((level, event, extra))

    monkeypatch.setattr(_security_watch, "read_account_twofa", _read)
    monkeypatch.setattr(_security_watch, "log_event", _log)
    return state


@pytest.mark.asyncio
async def test_the_first_read_stores_a_baseline_and_says_nothing(wire: _Wire) -> None:
    wire.status = TwoFactorStatusResult(has_password=True, has_recovery=True)

    await _security_watch.check_security("acc")

    assert wire.events == []
    stored = await fetch_security_snapshot("acc")
    assert stored is not None
    assert (stored.has_password, stored.has_recovery) == (True, True)


@pytest.mark.asyncio
async def test_a_pending_reset_is_reported_even_on_the_very_first_read(wire: _Wire) -> None:
    wire.status = TwoFactorStatusResult(has_password=True, pending_reset_date="2026-12-01T00:00:00")

    await _security_watch.check_security("acc")
    await _security_watch.check_security("acc")

    assert wire.events == [
        ("WARNING", "twofa_reset_pending", {"pending_reset_date": "2026-12-01T00:00:00"})
    ]


@pytest.mark.asyncio
async def test_a_reset_that_appears_later_is_reported_once(wire: _Wire) -> None:
    await _security_watch.check_security("acc")
    wire.status = TwoFactorStatusResult(pending_reset_date="2026-12-01T00:00:00")

    await _security_watch.check_security("acc")
    await _security_watch.check_security("acc")

    assert [event for _l, event, _e in wire.events] == ["twofa_reset_pending"]


@pytest.mark.asyncio
async def test_changed_flags_are_named_and_the_email_pattern_is_never_stored(wire: _Wire) -> None:
    wire.status = TwoFactorStatusResult(has_password=True, has_recovery=True)
    await _security_watch.check_security("acc")
    wire.status = TwoFactorStatusResult(
        has_password=True, has_recovery=False, email_unconfirmed_pattern="a***@mail.example"
    )

    await _security_watch.check_security("acc")

    assert wire.events == [
        ("WARNING", "security_settings_changed", {"changed": ["has_recovery", "email_unconfirmed"]})
    ]
    assert "a***" not in repr(wire.events)
    assert "a***" not in repr(await fetch_security_snapshot("acc"))


@pytest.mark.asyncio
async def test_our_own_change_forgets_the_snapshot_so_the_next_pass_rebaselines(
    wire: _Wire,
) -> None:
    await _security_watch.check_security("acc")
    await forget_security_snapshot("acc")
    wire.status = TwoFactorStatusResult(has_password=True)

    await _security_watch.check_security("acc")

    assert wire.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [AccountActionError("x"), RuntimeError("boom")])
async def test_an_unreadable_status_is_skipped_and_nothing_raises(
    wire: _Wire, failure: BaseException
) -> None:
    wire.error = failure
    await _security_watch.check_security("acc")
    assert wire.events == []
    assert await fetch_security_snapshot("acc") is None


@pytest.mark.asyncio
async def test_a_refused_read_with_no_status_stores_nothing(wire: _Wire) -> None:
    wire.status = None
    await _security_watch.check_security("acc")
    assert await fetch_security_snapshot("acc") is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("wire")
async def test_cancelling_a_pending_recovery_email_forgets_the_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.repositories.account_security_snapshots import save_security_snapshot  # noqa: PLC0415
    from schemas.security_snapshot import SecuritySnapshot  # noqa: PLC0415
    from services.accounts import _twofa_email  # noqa: PLC0415

    async def _run(_account_id: str, _action: object) -> None:
        return None

    monkeypatch.setattr(_twofa_email, "_run_email_action", _run)
    monkeypatch.setattr(_twofa_email, "read_account_twofa", lambda _a: _view())
    monkeypatch.setattr(_twofa_email, "log_event", wire_log := _noop_log)
    assert wire_log is _noop_log
    await save_security_snapshot(
        "acc", SecuritySnapshot(has_password=True, has_recovery=False, email_unconfirmed=True)
    )

    await _twofa_email.cancel_account_twofa_email("acc")

    assert await fetch_security_snapshot("acc") is None


async def _view() -> AccountTwoFactorView:
    return AccountTwoFactorView()


async def _noop_log(*_args: object, **_kwargs: object) -> None:
    return None
