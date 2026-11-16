"""An account without a proxy is never connected to Telegram, and refusing it changes nothing."""

from __future__ import annotations

import pytest

from core.config import settings
from core.db import fetch_account, update_account_from_session_check
from core.telegram_client import _client
from core.telegram_client._client import ProxyRequiredError
from schemas.accounts import AccountCheckRequest, AccountCreate
from schemas.device_fingerprint import TelegramClientProfile
from schemas.telegram_session import TelegramSessionCheckResult
from services import inbox_runtime
from services.accounts import AccountActionError, add_account, check_account_session
from services.accounts import sessions as sessions_service


@pytest.fixture(autouse=True)
def _enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    # The suite allows direct connections for its fakes; this file tests the real rule.
    monkeypatch.setattr(settings.telegram, "allow_direct_connection", False)


def test_no_client_is_built_for_an_account_without_a_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[object] = []
    monkeypatch.setattr(_client, "GuardedTelegramClient", lambda *a, **_k: built.append(a))

    with pytest.raises(ProxyRequiredError):
        _client.create_telegram_client(
            TelegramClientProfile.model_construct(proxy_type=None, proxy_host=None, proxy_port=None)
        )

    assert issubclass(ProxyRequiredError, ConnectionError)
    assert built == []


@pytest.mark.asyncio
async def test_checking_an_account_without_a_proxy_refuses_and_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_account(AccountCreate(account_id="acc-1"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="acc-1", session_path="acc-1", status="alive", is_temporary=False
        )
    )
    before = await fetch_account("acc-1")
    probes: list[object] = []

    async def _probe(request: object) -> object:
        probes.append(request)
        raise AssertionError

    monkeypatch.setattr(sessions_service, "check_telegram_session", _probe)

    with pytest.raises(AccountActionError) as refused:
        await check_account_session(AccountCheckRequest(account_id="acc-1"))

    assert refused.value.code == "proxy_required"
    assert probes == []
    after = await fetch_account("acc-1")
    assert after is not None
    assert before is not None
    assert (after.status, after.last_checked_at) == (before.status, before.last_checked_at)


@pytest.mark.asyncio
async def test_the_inbox_never_starts_for_an_account_without_a_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_account(AccountCreate(account_id="acc-1"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="acc-1", session_path="acc-1", status="alive", is_temporary=False
        )
    )
    subscribed: list[str] = []

    async def _subscribe(account_id: str, _handler: object) -> bool:
        subscribed.append(account_id)
        return True

    monkeypatch.setattr(inbox_runtime, "subscribe_incoming_messages", _subscribe)

    assert await inbox_runtime._start_account("acc-1") is False
    assert subscribed == []
