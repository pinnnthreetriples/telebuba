"""Contact-lookup read dispatcher — phones to Telegram users via ImportContacts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from telethon import errors
from telethon.tl.functions.contacts import ImportContactsRequest

from core.telegram_client import TelegramReadError, execute_read
from schemas.telegram_actions import LookupContactsByPhone
from schemas.telegram_actions_contacts import ContactLookupBatchResult
from tests.core.telegram_client.helpers import patch_read_client as _patch_client

if TYPE_CHECKING:
    from telethon.tl.types import InputPhoneContact


def _imported(client_id: int, user_id: int) -> SimpleNamespace:
    return SimpleNamespace(client_id=client_id, user_id=user_id)


def _user(user_id: int, **fields: object) -> SimpleNamespace:
    base: dict[str, object] = {"username": None, "first_name": None, "last_name": None}
    base.update(fields)
    return SimpleNamespace(id=user_id, **base)


class _FakeClient:
    """Callable client double: returns a canned ImportContacts reply, records the request."""

    def __init__(
        self,
        *,
        imported: list[SimpleNamespace] | None = None,
        users: list[SimpleNamespace] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.imported = imported or []
        self.users = users or []
        self.error = error
        self.requests: list[object] = []

    async def connect(self) -> None:
        return None

    async def __call__(self, request: object, /) -> object:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(imported=self.imported, users=self.users, retry_contacts=[])


def _sent_phones(client: _FakeClient) -> list[str]:
    request = client.requests[0]
    assert isinstance(request, ImportContactsRequest)
    contacts: list[InputPhoneContact] = list(request.contacts)
    return [contact.phone for contact in contacts]


@pytest.mark.asyncio
async def test_resolves_found_phones_and_reports_the_rest_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        imported=[_imported(client_id=0, user_id=111)],
        users=[_user(111, username="alice", first_name="Alice", last_name="A")],
    )
    _patch_client(monkeypatch, client)

    result = await execute_read(
        "acc-1", LookupContactsByPhone(phones=["+15551230000", "+15559999999"])
    )

    assert isinstance(result, ContactLookupBatchResult)
    assert [(m.phone, m.user_id, m.username) for m in result.matches] == [
        ("+15551230000", 111, "alice")
    ]
    assert result.matches[0].first_name == "Alice"
    assert result.unresolved == ["+15559999999"]


@pytest.mark.asyncio
async def test_client_id_maps_each_match_back_to_its_own_phone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only the second phone (index 1) resolved: the client_id must pick +200, not +100.
    client = _FakeClient(
        imported=[_imported(client_id=1, user_id=222)],
        users=[_user(222, username="bob")],
    )
    _patch_client(monkeypatch, client)

    result = await execute_read(
        "acc-1", LookupContactsByPhone(phones=["+15550000100", "+15550000200"])
    )

    assert isinstance(result, ContactLookupBatchResult)
    assert [m.phone for m in result.matches] == ["+15550000200"]
    assert result.unresolved == ["+15550000100"]


@pytest.mark.asyncio
async def test_unparseable_numbers_never_reach_the_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        imported=[_imported(client_id=1, user_id=333)],
        users=[_user(333)],
    )
    _patch_client(monkeypatch, client)

    result = await execute_read(
        "acc-1", LookupContactsByPhone(phones=["not-a-phone", "+15551234567"])
    )

    assert isinstance(result, ContactLookupBatchResult)
    # The junk entry is unresolved and the RPC only ever saw the real number.
    assert _sent_phones(client) == ["15551234567"]
    assert [m.phone for m in result.matches] == ["+15551234567"]
    assert result.unresolved == ["not-a-phone"]


@pytest.mark.asyncio
async def test_all_unparseable_skips_the_rpc_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    result = await execute_read("acc-1", LookupContactsByPhone(phones=["nope", "---"]))

    assert isinstance(result, ContactLookupBatchResult)
    assert result.matches == []
    assert result.unresolved == ["nope", "---"]
    assert client.requests == []


@pytest.mark.asyncio
async def test_flood_wait_rides_the_read_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(error=errors.FloodWaitError(request=None, capture=120))
    _patch_client(monkeypatch, client)

    with pytest.raises(TelegramReadError) as excinfo:
        await execute_read("acc-1", LookupContactsByPhone(phones=["+15551234567"]))

    assert excinfo.value.kind == "flood_wait"
    assert excinfo.value.seconds == 120
