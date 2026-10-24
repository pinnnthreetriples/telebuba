"""Passkey policy: vault key before Telegram, row after Telegram, removal in reverse."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.passkey_vault import PasskeyVaultError
from core.telegram_client import PasskeyGatewayError
from schemas.passkey import StoredPasskey
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import AccountActionError, AccountNotFoundError
from services.accounts import passkey as svc

_HANDLE = "1:257221571"


def _stored() -> StoredPasskey:
    return StoredPasskey(
        account_id="acc-1",
        credential_id="Y3JlZA",
        user_handle=_HANDLE,
        vault_key_name="tb-acc-1",
        created_at="2026-10-24T12:00:00+00:00",
    )


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:  # noqa: C901 - one fake per seam
    """Fake every seam ``services.accounts.passkey`` calls; ``state["log"]`` is their order."""
    state: dict[str, object] = {"row": None, "configured": True, "log": [], "events": []}
    log: list[object] = state["log"]

    async def _fetch_account(account_id: str) -> object:
        return None if account_id == "missing" else SimpleNamespace(session_name="sess-1")

    async def _fetch_row(_account_id: str) -> StoredPasskey | None:
        return state["row"]  # ty: ignore[invalid-return-type]

    async def _save_row(passkey: StoredPasskey) -> None:
        log.append("save")
        state["row"] = passkey

    async def _delete_row(_account_id: str) -> None:
        log.append("forget")
        state["row"] = None

    async def _create_key(name: str) -> str:
        log.append(f"vault_create:{name}")
        return "PEM"

    async def _delete_key(name: str) -> None:
        log.append(f"vault_delete:{name}")
        if isinstance(state.get("vault_error"), Exception):
            raise state["vault_error"]  # ty: ignore[invalid-raise]

    async def _register(account_id: str, *, public_key_pem: str, credential_id: str) -> str:
        log.append(f"register:{account_id}:{public_key_pem}")
        if isinstance(state.get("gateway_error"), Exception):
            raise state["gateway_error"]  # ty: ignore[invalid-raise]
        state["credential_id"] = credential_id
        return _HANDLE

    async def _delete_passkey(_account_id: str, credential_id: str) -> bool:
        log.append(f"telegram_delete:{credential_id}")
        if isinstance(state.get("gateway_error"), Exception):
            raise state["gateway_error"]  # ty: ignore[invalid-raise]
        return True

    async def _login(passkey: StoredPasskey, session_name: str | None) -> object:
        log.append(f"login:{passkey.credential_id}:{session_name}")
        return state["login_result"]

    async def _update(result: TelegramSessionCheckResult) -> object:
        log.append(f"update:{result.status}")
        return SimpleNamespace(account_id=result.account_id)

    async def _inbox(account_id: str) -> None:
        log.append(f"inbox:{account_id}")

    async def _log_event(level: str, event: str, **kwargs: object) -> None:
        state["events"].append((level, event, kwargs.get("extra")))  # ty: ignore[unresolved-attribute]

    for name, fake in {
        "fetch_account": _fetch_account,
        "fetch_account_passkey": _fetch_row,
        "save_account_passkey": _save_row,
        "delete_account_passkey": _delete_row,
        "create_signing_key": _create_key,
        "delete_signing_key": _delete_key,
        "register_passkey": _register,
        "delete_passkey": _delete_passkey,
        "login_with_passkey": _login,
        "update_account_from_session_check": _update,
        "start_account_inbox": _inbox,
        "log_event": _log_event,
    }.items():
        monkeypatch.setattr(svc, name, fake)
    monkeypatch.setattr(svc, "passkey_vault_configured", lambda: state["configured"])
    monkeypatch.setattr(svc, "vault_key_name", lambda account_id: f"tb-{account_id}")
    monkeypatch.setattr(svc, "credential_id_for", lambda pem: f"id-of-{pem}")
    return state


def _result(status: str, **fields: object) -> TelegramSessionCheckResult:
    return TelegramSessionCheckResult(
        account_id="acc-1",
        session_path="sessions/sess-1",
        status=status,  # ty: ignore[invalid-argument-type]
        is_temporary=False,
        **fields,  # ty: ignore[invalid-argument-type]
    )


@pytest.mark.asyncio
async def test_the_view_says_whether_a_passkey_exists(wire: dict[str, object]) -> None:
    view = await svc.read_account_passkey("acc-1")
    assert (view.has_passkey, view.created_at, view.vault_configured) == (False, None, True)

    wire["row"] = _stored()
    view = await svc.read_account_passkey("acc-1")
    assert (view.has_passkey, view.created_at) == (True, "2026-10-24T12:00:00+00:00")


@pytest.mark.asyncio
async def test_creating_makes_the_vault_key_first_and_stores_the_row_last(
    wire: dict[str, object],
) -> None:
    view = await svc.create_account_passkey("acc-1")

    assert view.has_passkey is True
    assert wire["log"] == ["vault_create:tb-acc-1", "register:acc-1:PEM", "save"]
    row: StoredPasskey = wire["row"]  # ty: ignore[invalid-assignment]
    assert row.user_handle == _HANDLE
    assert row.vault_key_name == "tb-acc-1"
    assert row.credential_id == wire["credential_id"] == "id-of-PEM"
    assert ("INFO", "passkey_created", None) in wire["events"]  # ty: ignore[unsupported-operator]


@pytest.mark.parametrize(
    ("error", "code", "retry"),
    [
        (PasskeyGatewayError("flood_wait", retry_after_seconds=60), "flood_wait", 60),
        (PasskeyGatewayError("passkey_telegram_refused"), "passkey_telegram_refused", None),
        (PasskeyVaultError("passkey_vault_unreachable"), "passkey_vault_unreachable", None),
    ],
)
@pytest.mark.asyncio
async def test_a_refused_creation_stores_nothing(
    wire: dict[str, object], error: Exception, code: str, retry: int | None
) -> None:
    wire["gateway_error"] = error
    with pytest.raises(AccountActionError) as caught:
        await svc.create_account_passkey("acc-1")
    assert caught.value.code == code
    assert caught.value.retry_after_seconds == retry
    assert wire["row"] is None
    assert ("WARNING", "passkey_create_failed", {"code": code}) in wire["events"]  # ty: ignore[unsupported-operator]


@pytest.mark.asyncio
async def test_a_second_passkey_is_refused(wire: dict[str, object]) -> None:
    wire["row"] = _stored()
    with pytest.raises(AccountActionError, match="passkey_exists"):
        await svc.create_account_passkey("acc-1")
    assert wire["log"] == []


@pytest.mark.parametrize(
    "call",
    [
        svc.create_account_passkey,
        svc.relogin_with_passkey,
        svc.remove_account_passkey,
    ],
)
@pytest.mark.asyncio
async def test_without_a_vault_every_write_refuses(wire: dict[str, object], call: object) -> None:
    wire["configured"] = False
    wire["row"] = _stored()
    with pytest.raises(AccountActionError, match="passkey_vault_disabled"):
        await call("acc-1")  # ty: ignore[call-non-callable]
    assert wire["log"] == []


@pytest.mark.parametrize(
    "call",
    [
        svc.read_account_passkey,
        svc.create_account_passkey,
        svc.relogin_with_passkey,
        svc.remove_account_passkey,
    ],
)
@pytest.mark.usefixtures("wire")
@pytest.mark.asyncio
async def test_an_unknown_account_is_not_found(call: object) -> None:
    with pytest.raises(AccountNotFoundError):
        await call("missing")  # ty: ignore[call-non-callable]


@pytest.mark.parametrize("call", [svc.relogin_with_passkey, svc.remove_account_passkey])
@pytest.mark.usefixtures("wire")
@pytest.mark.asyncio
async def test_without_a_passkey_login_and_removal_refuse(call: object) -> None:
    with pytest.raises(AccountActionError, match="passkey_missing"):
        await call("acc-1")  # ty: ignore[call-non-callable]


@pytest.mark.asyncio
async def test_a_passkey_login_marks_the_account_alive(wire: dict[str, object]) -> None:
    wire["row"] = _stored()
    wire["login_result"] = _result("alive", user_id=257221571)

    view = await svc.relogin_with_passkey("acc-1")

    assert view.has_passkey is True
    assert wire["log"] == ["login:Y3JlZA:sess-1", "update:alive", "inbox:acc-1"]


@pytest.mark.parametrize(
    ("result", "code", "retry"),
    [
        (
            _result("unauthorized", error_message="passkey_twofa_missing"),
            "passkey_twofa_missing",
            None,
        ),
        (_result("flood_wait", flood_wait_seconds=90), "flood_wait", 90),
        (_result("unknown_error", error_message="ConnectionError"), "unknown_error", None),
        (_result("unauthorized"), "passkey_login_failed", None),
    ],
)
@pytest.mark.asyncio
async def test_a_failed_passkey_login_changes_nothing(
    wire: dict[str, object],
    result: TelegramSessionCheckResult,
    code: str,
    retry: int | None,
) -> None:
    wire["row"] = _stored()
    wire["login_result"] = result

    with pytest.raises(AccountActionError) as caught:
        await svc.relogin_with_passkey("acc-1")

    assert caught.value.code == code
    assert caught.value.retry_after_seconds == retry
    assert wire["log"] == ["login:Y3JlZA:sess-1"]


@pytest.mark.asyncio
async def test_removal_asks_telegram_first_and_deletes_the_vault_key_last(
    wire: dict[str, object],
) -> None:
    wire["row"] = _stored()

    view = await svc.remove_account_passkey("acc-1")

    assert view.has_passkey is False
    assert wire["log"] == ["telegram_delete:Y3JlZA", "forget", "vault_delete:tb-acc-1"]


@pytest.mark.asyncio
async def test_forgetting_leaves_telegram_alone_and_still_kills_the_vault_key(
    wire: dict[str, object],
) -> None:
    wire["row"] = _stored()

    view = await svc.remove_account_passkey("acc-1", forget_only=True)

    assert view.has_passkey is False
    assert wire["log"] == ["forget", "vault_delete:tb-acc-1"]
    assert ("INFO", "passkey_removed", {"forget_only": True}) in wire["events"]  # ty: ignore[unsupported-operator]


@pytest.mark.asyncio
async def test_a_telegram_refusal_keeps_the_passkey(wire: dict[str, object]) -> None:
    wire["row"] = _stored()
    wire["gateway_error"] = PasskeyGatewayError("unavailable")

    with pytest.raises(AccountActionError, match="unavailable"):
        await svc.remove_account_passkey("acc-1")

    assert wire["row"] == _stored()
    assert wire["log"] == ["telegram_delete:Y3JlZA"]


@pytest.mark.asyncio
async def test_a_vault_refusal_after_the_removal_is_reported(wire: dict[str, object]) -> None:
    wire["row"] = _stored()
    wire["vault_error"] = PasskeyVaultError("passkey_vault_rejected", "http_403")

    with pytest.raises(AccountActionError, match="passkey_vault_rejected"):
        await svc.remove_account_passkey("acc-1")

    assert wire["row"] is None
