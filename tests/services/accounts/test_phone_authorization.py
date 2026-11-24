"""The phone app's authorization is recorded as ours, and never silently re-created.

The scripted container (``_mobile_support``) is the phone; ``_Telegram`` is the account's
authorization list. The sign-in's baseline is read before the number is typed, the record
after the chat list shows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.emulator.redroid import Instance
from core.emulator.ui_dump import UiNode
from core.repositories.account_own_authorizations import (
    fetch_own_authorizations,
    save_own_authorizations,
)
from core.telegram_client import SessionAuditGatewayError
from schemas.session_audit import AccountSession, AuthorizationList
from services.accounts import _emulator_sign_in
from services.accounts import _phone_authorization as phone_module
from services.accounts import mobile_emulator as emulator_module
from services.accounts import own_authorizations as own_module
from services.accounts._session_classify import mark_own
from services.accounts.mobile_emulator import EmulatorRefusedError, open_account_mobile
from tests.services.accounts._mobile_support import _account, _Container

if TYPE_CHECKING:
    from core.emulator.ui_dump import Screen

pytestmark = pytest.mark.usefixtures("mobile_enabled")

# A new pod: the open reads nothing before the sign-in does. Warming reads the screen first.
_NEW_POD: list[Screen] = ["intro", "phone", "confirm", "code", "password", "chats"]
_WARMING: list[Screen] = ["intro", *_NEW_POD]
_AT = "2026-11-01T00:00:00+00:00"
_Events = list[tuple[str, dict[str, object]]]


def _session(
    hash_: str,
    *,
    platform: str = "Android",
    official: bool = True,
    ours: bool = False,
    current: bool = False,
) -> AccountSession:
    return AccountSession(
        hash=hash_,
        is_current=current,
        is_ours=ours,
        official_app=official,
        device_model="Pixel",
        platform=platform,
        system_version="14",
        app_name="Telegram Android" if platform == "Android" else "Telegram Desktop",
        ip="192.0.2.1",
        country="Nowhere",
    )


def _primary() -> AccountSession:
    return _session("1", current=True, ours=True)


class _Telegram:
    """The authorization list: ``reads`` are returned in turn (the last one repeats)."""

    def __init__(self) -> None:
        self.reads: list[list[AccountSession]] = [[_primary()]]
        self.fails = False
        self.count = 0

    async def read(
        self, _account_id: str, _devices: list[str], own: dict[str, str] | None = None
    ) -> AuthorizationList:
        self.count += 1
        if self.fails:
            code = "unavailable"
            raise SessionAuditGatewayError(code, error_type="OSError")
        sessions = self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]
        return AuthorizationList(sessions=mark_own(sessions, own or {}), ttl_days=180)


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> _Telegram:
    wire = _Telegram()
    monkeypatch.setattr(own_module, "read_account_sessions", wire.read)
    return wire


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> _Events:
    logged: _Events = []

    async def fake_log(
        _level: str,
        event: str,
        *,
        account_id: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        del account_id
        logged.append((event, extra or {}))

    monkeypatch.setattr(phone_module, "log_event", fake_log)
    return logged


@pytest.fixture(autouse=True)
def _no_holds() -> None:
    emulator_module._OPERATOR.clear()


def _names(events: _Events) -> list[str]:
    return [name for name, _extra in events]


@pytest.mark.asyncio
async def test_one_new_official_android_authorization_is_recorded_as_phone(
    container: _Container, telegram: _Telegram, events: _Events
) -> None:
    await _account()
    container.screens = list(_NEW_POD)
    telegram.reads = [[_primary()], [_primary(), _session("77"), _session("5", platform="Desktop")]]

    result = await open_account_mobile("acc")

    assert result.signed_in is True
    assert await fetch_own_authorizations("acc") == {"77": "phone"}
    assert ("own_phone_authorization_recorded", {"hash_suffix": "77"}) in events


@pytest.mark.parametrize(
    ("after", "candidates"),
    [
        ([_primary()], 0),
        ([_primary(), _session("8"), _session("9")], 2),
        ([_primary(), _session("8", official=False)], 0),
    ],
)
@pytest.mark.asyncio
async def test_zero_or_several_candidates_record_nothing_and_warn(
    container: _Container,
    telegram: _Telegram,
    events: _Events,
    after: list[AccountSession],
    candidates: int,
) -> None:
    await _account()
    container.screens = list(_NEW_POD)
    telegram.reads = [[_primary()], after]

    assert (await open_account_mobile("acc")).signed_in is True

    assert await fetch_own_authorizations("acc") == {}
    assert ("own_phone_login_not_recorded", {"candidates": candidates, "baseline": True}) in events


@pytest.mark.asyncio
async def test_an_authorization_listed_before_the_number_is_never_a_candidate(
    container: _Container, telegram: _Telegram, events: _Events
) -> None:
    await _account()
    container.screens = list(_NEW_POD)
    telegram.reads = [[_primary(), _session("8")]]  # the same listing before and after

    await open_account_mobile("acc")

    assert await fetch_own_authorizations("acc") == {}
    assert "own_phone_login_not_recorded" in _names(events)


@pytest.mark.asyncio
async def test_a_list_that_cannot_be_read_never_fails_the_sign_in(
    container: _Container, telegram: _Telegram, events: _Events
) -> None:
    await _account()
    container.screens = list(_NEW_POD)
    telegram.fails = True

    result = await open_account_mobile("acc")

    assert result.signed_in is True
    assert await fetch_own_authorizations("acc") == {}
    assert "own_phone_authorization_recorded" not in _names(events)


@pytest.mark.asyncio
async def test_an_app_already_signed_in_makes_and_records_nothing(
    container: _Container, telegram: _Telegram
) -> None:
    await _account()
    container.screens = ["chats"]

    await open_account_mobile("acc")

    assert telegram.count == 0


def test_the_phone_kind_is_ours_and_not_foreign() -> None:
    marked = mark_own([_session("77"), _session("78")], {"77": "phone"})

    assert [(s.hash, s.is_ours, s.kind) for s in marked] == [
        ("77", True, "phone"),
        ("78", False, None),
    ]


@pytest.mark.parametrize("background", [True, False])
@pytest.mark.parametrize("still_listed", [True, False])
@pytest.mark.asyncio
async def test_a_recorded_authorization_and_an_entry_screen_is_settled(
    container: _Container,
    telegram: _Telegram,
    events: _Events,
    *,
    background: bool,
    still_listed: bool,
) -> None:
    await _account()
    await save_own_authorizations("acc", ["77"], kind="phone", recorded_at=_AT)
    container.instance = Instance(1, "telebuba-acc", running=True)
    container.screens = ["phone", "phone", "confirm", "code", "password", "chats"]
    before = [_primary(), _session("77")] if still_listed else [_primary()]
    telegram.reads = [before, [_primary()], [_primary(), _session("88")]]

    if background:
        with pytest.raises(EmulatorRefusedError) as refused:
            await open_account_mobile("acc", background=True)
        assert refused.value.code == "emulator_signed_out"
        assert "cutoff" not in container.steps, "no code was asked for"
        assert "phone_signed_out" in _names(events)
        assert await fetch_own_authorizations("acc") == {}
    else:
        assert (await open_account_mobile("acc")).signed_in is True
        assert "phone_signed_out" not in _names(events)
        assert await fetch_own_authorizations("acc") == {"88": "phone"}

    orphaned = ("phone_authorization_orphaned", {"hash_suffix": "77"}) in events
    assert orphaned is still_listed


@pytest.mark.asyncio
async def test_without_a_recorded_authorization_warming_signs_in_as_ever(
    container: _Container, telegram: _Telegram, events: _Events
) -> None:
    await _account()
    container.screens = list(_WARMING)
    telegram.reads = [[_primary()], [_primary(), _session("88")]]

    result = await open_account_mobile("acc", background=True)

    assert result.signed_in is True
    assert "phone_signed_out" not in _names(events)
    assert await fetch_own_authorizations("acc") == {"88": "phone"}


_DIALOG = UiNode(
    text="Too many attempts, please try again later.",
    description="",
    class_name="android.widget.TextView",
    password=False,
    bounds=(100, 1000, 980, 1200),
)


@pytest.mark.parametrize(
    ("nodes", "code"),
    [([_DIALOG], "emulator_code_refused"), ([], "emulator_field_missing")],
)
@pytest.mark.asyncio
async def test_a_refused_code_request_says_so_and_an_unknown_screen_does_not(
    container: _Container,
    telegram: _Telegram,  # noqa: ARG001 - keeps the sign-in off the real Telegram gateway
    monkeypatch: pytest.MonkeyPatch,
    nodes: list[UiNode],
    code: str,
) -> None:
    await _account()
    container.screens = list(_NEW_POD)
    reads = iter([("intro", []), ("phone", []), ("phone", nodes)])

    async def wait(_serial: str, _wanted: set[Screen]) -> tuple[Screen, list[UiNode]]:
        return next(reads)  # ty: ignore[invalid-return-type]

    monkeypatch.setattr(_emulator_sign_in, "wait_screen", wait)

    result = await open_account_mobile("acc")

    assert (result.signed_in, result.sign_in_refusal) == (False, code)
