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
        if account_id == "missing" or state.get("account_gone"):
            return None
        return SimpleNamespace(session_name="sess-1")

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
        state["account_gone"] = bool(state.get("gone_after_register"))
        return _HANDLE

    async def _delete_passkey(_account_id: str, credential_id: str) -> bool:
        log.append(f"telegram_delete:{credential_id}")
        if isinstance(state.get("gateway_error"), Exception):
            raise state["gateway_error"]  # ty: ignore[invalid-raise]
        return True

    async def _is_listed(_account_id: str, credential_id: str) -> bool:
        log.append(f"telegram_list:{credential_id}")
        if isinstance(state.get("gateway_error"), Exception):
            raise state["gateway_error"]  # ty: ignore[invalid-raise]
        return bool(state.get("listed"))

    async def _key_exists(name: str) -> bool:
        log.append(f"vault_exists:{name}")
        if isinstance(state.get("exists_error"), Exception):
            raise state["exists_error"]  # ty: ignore[invalid-raise]
        return bool(state.get("key_exists", True))

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
        "signing_key_exists": _key_exists,
        "register_passkey": _register,
        "delete_passkey": _delete_passkey,
        "is_passkey_listed": _is_listed,
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
        svc.check_account_passkey,
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


@pytest.mark.parametrize(
    "call", [svc.check_account_passkey, svc.relogin_with_passkey, svc.remove_account_passkey]
)
@pytest.mark.asyncio
async def test_without_a_passkey_check_login_and_removal_refuse(
    wire: dict[str, object], call: object
) -> None:
    with pytest.raises(AccountActionError, match="passkey_missing"):
        await call("acc-1")  # ty: ignore[call-non-callable]
    assert wire["log"] == []


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
async def test_removal_asks_telegram_first_then_the_vault_key_then_the_row(
    wire: dict[str, object],
) -> None:
    wire["row"] = _stored()

    view = await svc.remove_account_passkey("acc-1")

    assert view.has_passkey is False
    assert wire["log"] == ["telegram_delete:Y3JlZA", "vault_delete:tb-acc-1", "forget"]


@pytest.mark.asyncio
async def test_forgetting_leaves_telegram_alone_and_still_kills_the_vault_key_first(
    wire: dict[str, object],
) -> None:
    wire["row"] = _stored()

    view = await svc.remove_account_passkey("acc-1", forget_only=True)

    assert view.has_passkey is False
    assert wire["log"] == ["vault_delete:tb-acc-1", "forget"]
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
async def test_a_vault_refusal_keeps_the_row_so_a_retry_converges(
    wire: dict[str, object],
) -> None:
    wire["row"] = _stored()
    wire["vault_error"] = PasskeyVaultError("passkey_vault_sealed", "http_503")

    with pytest.raises(AccountActionError, match="passkey_vault_sealed"):
        await svc.remove_account_passkey("acc-1")

    assert wire["row"] == _stored()
    assert "forget" not in wire["log"]  # ty: ignore[unsupported-operator]


@pytest.mark.parametrize(("listed", "state"), [(True, "present"), (False, "missing")])
@pytest.mark.asyncio
async def test_a_successful_list_decides_present_or_missing(
    wire: dict[str, object],
    listed: bool,  # noqa: FBT001 - parametrized
    state: str,
) -> None:
    wire["row"] = _stored()
    wire["listed"] = listed

    check = await svc.check_account_passkey("acc-1")

    assert (check.account_id, check.telegram_state, check.reason) == ("acc-1", state, None)
    assert wire["log"] == ["telegram_list:Y3JlZA"]
    assert wire["row"] == _stored()
    assert ("INFO", "passkey_checked", {"state": state, "reason": None}) in wire["events"]  # ty: ignore[unsupported-operator]


@pytest.mark.parametrize("code", ["unavailable", "passkey_telegram_refused"])
@pytest.mark.asyncio
async def test_an_unreachable_telegram_or_dead_session_is_unknown_never_missing(
    wire: dict[str, object], code: str
) -> None:
    wire["row"] = _stored()
    wire["listed"] = False
    wire["gateway_error"] = PasskeyGatewayError(code)  # ty: ignore[invalid-argument-type]

    check = await svc.check_account_passkey("acc-1")

    assert (check.telegram_state, check.reason) == ("unknown", code)
    assert wire["row"] == _stored()
    assert ("INFO", "passkey_checked", {"state": "unknown", "reason": code}) in wire["events"]  # ty: ignore[unsupported-operator]


@pytest.mark.asyncio
async def test_a_flood_wait_is_the_retry_after_refusal_not_missing(
    wire: dict[str, object],
) -> None:
    wire["row"] = _stored()
    wire["gateway_error"] = PasskeyGatewayError("flood_wait", retry_after_seconds=60)

    with pytest.raises(AccountActionError) as caught:
        await svc.check_account_passkey("acc-1")

    assert (caught.value.code, caught.value.retry_after_seconds) == ("flood_wait", 60)
    assert wire["row"] == _stored()
    assert ("WARNING", "passkey_check_failed", {"code": "flood_wait"}) in wire["events"]  # ty: ignore[unsupported-operator]


@pytest.mark.asyncio
async def test_the_check_needs_no_vault(wire: dict[str, object]) -> None:
    wire["configured"] = False
    wire["row"] = _stored()
    wire["listed"] = True

    assert (await svc.check_account_passkey("acc-1")).telegram_state == "present"


@pytest.mark.asyncio
async def test_an_account_removed_during_creation_gets_no_row_but_keeps_the_vault_key(
    wire: dict[str, object],
) -> None:
    wire["gone_after_register"] = True

    with pytest.raises(AccountNotFoundError):
        await svc.create_account_passkey("acc-1")

    assert wire["row"] is None
    assert wire["log"] == [
        "vault_create:tb-acc-1",
        "register:acc-1:PEM",
    ]  # Telegram may already list the passkey: the key is the only way to ever use it


@pytest.mark.asyncio
async def test_a_failed_creation_journals_the_non_secret_vault_detail(
    wire: dict[str, object],
) -> None:
    wire["gateway_error"] = PasskeyGatewayError("passkey_vault_sealed", error_type="http_503")

    with pytest.raises(AccountActionError, match="passkey_vault_sealed"):
        await svc.create_account_passkey("acc-1")

    assert (
        "WARNING",
        "passkey_create_failed",
        {"code": "passkey_vault_sealed", "detail": "http_503"},
    ) in wire["events"]  # ty: ignore[unsupported-operator]


@pytest.mark.asyncio
async def test_a_failed_relogin_journals_the_vault_detail(wire: dict[str, object]) -> None:
    wire["row"] = _stored()
    wire["login_result"] = _result(
        "unauthorized", error_message="passkey_vault_sealed", error_type="http_503"
    )

    with pytest.raises(AccountActionError, match="passkey_vault_sealed"):
        await svc.relogin_with_passkey("acc-1")

    assert (
        "WARNING",
        "passkey_login_failed",
        {"code": "passkey_vault_sealed", "detail": "http_503"},
    ) in wire["events"]  # ty: ignore[unsupported-operator]


@pytest.mark.asyncio
async def test_the_vault_check_reports_a_present_key_as_ok(wire: dict[str, object]) -> None:
    wire["row"] = _stored()

    assert await svc.check_passkey_vault("acc-1") == "ok"
    assert svc.passkey_vault_state("acc-1") == "ok"
    assert wire["events"] == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (PasskeyVaultError("passkey_vault_sealed", "http_503"), "sealed"),
        (PasskeyVaultError("passkey_vault_forbidden", "http_403"), "forbidden"),
        (PasskeyVaultError("passkey_vault_unreachable", "ConnectError"), "unreachable"),
        (PasskeyVaultError("passkey_vault_rejected", "key_rotated"), "rejected"),
    ],
)
@pytest.mark.asyncio
async def test_a_vault_that_cannot_answer_is_reported_before_the_operator_needs_it(
    wire: dict[str, object], error: Exception, expected: str
) -> None:
    wire["row"] = _stored()
    wire["exists_error"] = error

    assert await svc.check_passkey_vault("acc-1") == expected
    assert svc.passkey_vault_state("acc-1") == expected
    ((level, event, extra),) = wire["events"]  # ty: ignore[not-iterable]
    assert (level, event) == ("WARNING", "passkey_vault_unhealthy")
    assert extra["state"] == expected


@pytest.mark.asyncio
async def test_a_deleted_vault_key_is_reported_as_missing(wire: dict[str, object]) -> None:
    wire["row"] = _stored()
    wire["key_exists"] = False

    assert await svc.check_passkey_vault("acc-1") == "key_missing"
    assert ("WARNING", "passkey_vault_unhealthy", {"state": "key_missing"}) in wire["events"]  # ty: ignore[unsupported-operator]


@pytest.mark.asyncio
async def test_the_vault_check_has_nothing_to_do_without_a_passkey_or_a_vault(
    wire: dict[str, object],
) -> None:
    assert await svc.check_passkey_vault("acc-1") is None
    wire["row"] = _stored()
    wire["configured"] = False
    assert await svc.check_passkey_vault("acc-1") is None
    assert wire["log"] == []


@pytest.mark.asyncio
async def test_the_view_carries_the_last_vault_check_only_for_an_account_with_a_passkey(
    wire: dict[str, object],
) -> None:
    wire["row"] = _stored()
    wire["exists_error"] = PasskeyVaultError("passkey_vault_sealed", "http_503")
    await svc.check_passkey_vault("acc-1")

    assert (await svc.read_account_passkey("acc-1")).vault_state == "sealed"

    wire["row"] = None
    assert (await svc.read_account_passkey("acc-1")).vault_state is None
