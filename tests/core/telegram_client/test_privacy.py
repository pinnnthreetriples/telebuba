"""Account-privacy dispatch tests — ``account.getPrivacy`` / ``account.setPrivacy``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from telethon import errors
from telethon.tl.functions.account import GetPrivacyRequest, SetPrivacyRequest
from telethon.tl.types import (
    InputPrivacyKeyAbout,
    InputPrivacyKeyProfilePhoto,
    InputPrivacyKeyStatusTimestamp,
    InputPrivacyValueAllowAll,
    InputPrivacyValueAllowContacts,
    InputPrivacyValueDisallowAll,
    PrivacyValueAllowAll,
    PrivacyValueAllowCloseFriends,
    PrivacyValueAllowContacts,
    PrivacyValueDisallowAll,
    PrivacyValueDisallowUsers,
)

from core.telegram_client import TelegramReadError, execute, execute_read
from core.telegram_client._privacy import _level_from_rules
from schemas.telegram_actions import GetPrivacySettings, SetPrivacySettings
from schemas.telegram_actions_privacy import PrivacySettingsResult
from tests.core.telegram_client.helpers import (
    patch_action_client,
)
from tests.core.telegram_client.helpers import (
    patch_read_client as _patch_read_client,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class _RuleClient:
    """Answers every ``GetPrivacyRequest`` from a per-key rule map."""

    def __init__(self, rules_by_key: dict[type, Sequence[object]]) -> None:
        self._rules_by_key = rules_by_key
        self.keys: list[type] = []

    async def connect(self) -> None:
        return None

    async def __call__(self, request: object) -> object:
        assert isinstance(request, GetPrivacyRequest)
        key_type = type(request.key)
        self.keys.append(key_type)
        return _Rules(self._rules_by_key.get(key_type, []))


class _Rules:
    def __init__(self, rules: Sequence[object]) -> None:
        self.rules = list(rules)


def _all_keys(rules: Sequence[object]) -> dict[type, Sequence[object]]:
    return {
        InputPrivacyKeyProfilePhoto: rules,
        InputPrivacyKeyAbout: rules,
        InputPrivacyKeyStatusTimestamp: rules,
    }


@pytest.mark.asyncio
async def test_get_privacy_settings_reads_all_three_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One ``getPrivacy`` per key, each mapped onto its own field."""
    client = _RuleClient(
        {
            InputPrivacyKeyProfilePhoto: [PrivacyValueAllowAll()],
            InputPrivacyKeyAbout: [PrivacyValueAllowContacts()],
            InputPrivacyKeyStatusTimestamp: [PrivacyValueDisallowAll()],
        },
    )
    _patch_read_client(monkeypatch, client)

    result = await execute_read("acc-priv", GetPrivacySettings())

    assert isinstance(result, PrivacySettingsResult)
    assert result.profile_photo == "everybody"
    assert result.bio == "contacts"
    assert result.last_seen == "nobody"
    assert client.keys == [
        InputPrivacyKeyProfilePhoto,
        InputPrivacyKeyAbout,
        InputPrivacyKeyStatusTimestamp,
    ]


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        (PrivacyValueAllowAll(), "everybody"),
        (PrivacyValueAllowContacts(), "contacts"),
        (PrivacyValueDisallowAll(), "nobody"),
    ],
)
@pytest.mark.asyncio
async def test_get_privacy_settings_maps_each_base_rule(
    monkeypatch: pytest.MonkeyPatch,
    rule: object,
    expected: str,
) -> None:
    _patch_read_client(monkeypatch, _RuleClient(_all_keys([rule])))

    result = await execute_read("acc-priv", GetPrivacySettings())

    assert isinstance(result, PrivacySettingsResult)
    assert (result.profile_photo, result.bio, result.last_seen) == (expected,) * 3


@pytest.mark.asyncio
async def test_get_privacy_settings_unknown_rule_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A base rule we do not model (close friends) is reported, not guessed."""
    _patch_read_client(monkeypatch, _RuleClient(_all_keys([PrivacyValueAllowCloseFriends()])))

    result = await execute_read("acc-priv", GetPrivacySettings())

    assert isinstance(result, PrivacySettingsResult)
    assert (result.profile_photo, result.bio, result.last_seen) == ("unknown",) * 3


@pytest.mark.asyncio
async def test_get_privacy_settings_empty_rules_are_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_read_client(monkeypatch, _RuleClient(_all_keys([])))

    result = await execute_read("acc-priv", GetPrivacySettings())

    assert isinstance(result, PrivacySettingsResult)
    assert (result.profile_photo, result.bio, result.last_seen) == ("unknown",) * 3


@pytest.mark.asyncio
async def test_get_privacy_settings_exception_rules_keep_the_base_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-user exception rules ride alongside the base rule; the base rule wins."""
    rules = [PrivacyValueDisallowUsers(users=[1, 2]), PrivacyValueAllowContacts()]
    _patch_read_client(monkeypatch, _RuleClient(_all_keys(rules)))

    result = await execute_read("acc-priv", GetPrivacySettings())

    assert isinstance(result, PrivacySettingsResult)
    assert (result.profile_photo, result.bio, result.last_seen) == ("contacts",) * 3


def test_level_from_rules_rejects_a_non_list() -> None:
    """A layer change upstream yields ``unknown``, never an exception."""
    assert _level_from_rules(None) == "unknown"
    assert _level_from_rules(object()) == "unknown"


@pytest.mark.asyncio
async def test_get_privacy_settings_rpc_error_becomes_read_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reads ride the existing ladder: RPCError → the project's typed read error."""

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            raise errors.RPCError(request=None, message="boom", code=400)

    _patch_read_client(monkeypatch, FakeClient())

    with pytest.raises(TelegramReadError):
        await execute_read("acc-priv", GetPrivacySettings())


class _SetClient:
    async def connect(self) -> None:
        return None

    def __init__(self) -> None:
        self.requests: list[SetPrivacyRequest] = []

    async def __call__(self, request: object) -> object:
        assert isinstance(request, SetPrivacyRequest)
        self.requests.append(request)
        return object()


@pytest.mark.asyncio
async def test_set_privacy_settings_sends_one_request_per_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _SetClient()
    patch_action_client(monkeypatch, client)

    result = await execute(
        "acc-priv",
        SetPrivacySettings(profile_photo="everybody", bio="contacts", last_seen="nobody"),
    )

    assert result.status == "ok"
    assert [type(request.key) for request in client.requests] == [
        InputPrivacyKeyProfilePhoto,
        InputPrivacyKeyAbout,
        InputPrivacyKeyStatusTimestamp,
    ]
    assert [type(request.rules[0]) for request in client.requests] == [
        InputPrivacyValueAllowAll,
        InputPrivacyValueAllowContacts,
        InputPrivacyValueDisallowAll,
    ]


@pytest.mark.asyncio
async def test_set_privacy_settings_skips_none_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``None`` = unchanged: that key must not be sent to Telegram at all."""
    client = _SetClient()
    patch_action_client(monkeypatch, client)

    result = await execute("acc-priv", SetPrivacySettings(bio="everybody"))

    assert result.status == "ok"
    assert len(client.requests) == 1
    assert isinstance(client.requests[0].key, InputPrivacyKeyAbout)
    assert isinstance(client.requests[0].rules[0], InputPrivacyValueAllowAll)


def test_set_privacy_settings_rejects_an_all_none_action() -> None:
    """An action that would change nothing must never reach Telegram."""
    with pytest.raises(ValueError, match="at least one of"):
        SetPrivacySettings()


@pytest.mark.asyncio
async def test_set_privacy_settings_rpc_error_is_a_failed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writes ride ``execute``'s ladder: no exception escapes, the result is typed."""

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            raise errors.RPCError(request=None, message="nope", code=400)

    patch_action_client(monkeypatch, FakeClient())

    result = await execute("acc-priv", SetPrivacySettings(bio="everybody"))

    assert result.status == "failed"
    assert result.action_type == "set_privacy_settings"
    assert result.error_type == "RPCError"


@pytest.mark.asyncio
async def test_set_privacy_settings_flood_does_not_mark_the_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deliberately outside ``_PROFILE_EDIT_ACTION_TYPES`` — no sticky DB status.

    A fleet-wide apply must not be able to park a large slice of the fleet in
    ``flood_wait`` (that would block start_warming across it).
    """
    marked: list[tuple[str, str]] = []

    async def _mark(account_id: str, status: str) -> None:
        marked.append((account_id, status))

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            raise errors.FloodWaitError(request=None)

    patch_action_client(monkeypatch, FakeClient())
    monkeypatch.setattr("core.telegram_client._actions._mark_account_status", _mark)

    result = await execute("acc-priv", SetPrivacySettings(bio="everybody"))

    assert result.status == "flood_wait"
    assert marked == []
