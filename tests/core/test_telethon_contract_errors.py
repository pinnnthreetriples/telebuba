"""Telethon contract: the error classes our ladders order and classify by."""

from __future__ import annotations

import pytest
from telethon import errors
from telethon.tl import functions, types

from core.telegram_client._authorized import probe_authorized
from core.telegram_client._profile import _DEAD_SESSION_ERROR_CODES
from core.telegram_client._session import _ACCOUNT_ERRORS, _SESSION_ERRORS
from core.telegram_client._session_audit import _PROVEN_DEAD_KEY

# ---------------------------------------------------------------------------------------
# errors: the classes our ladders order and classify by
# ---------------------------------------------------------------------------------------

_BASES = (
    (errors.AuthKeyUnregisteredError, errors.UnauthorizedError),
    (errors.AuthKeyInvalidError, errors.UnauthorizedError),
    (errors.AuthKeyPermEmptyError, errors.UnauthorizedError),
    (errors.SessionExpiredError, errors.UnauthorizedError),
    (errors.SessionRevokedError, errors.UnauthorizedError),
    (errors.UserDeactivatedError, errors.UnauthorizedError),
    (errors.UserDeactivatedBanError, errors.UnauthorizedError),
    (errors.SessionPasswordNeededError, errors.UnauthorizedError),
    (errors.AuthKeyDuplicatedError, errors.AuthKeyError),
    (errors.FreshResetAuthorisationForbiddenError, errors.AuthKeyError),
    (errors.FilerefUpgradeNeededError, errors.AuthKeyError),
    (errors.InputUserDeactivatedError, errors.BadRequestError),
    (errors.PasswordHashInvalidError, errors.BadRequestError),
    (errors.HashInvalidError, errors.BadRequestError),
    (errors.FrozenParticipantMissingError, errors.BadRequestError),
    (errors.PeerFloodError, errors.BadRequestError),
    (errors.FloodWaitError, errors.FloodError),
    (errors.FrozenMethodInvalidError, errors.FloodError),
    (errors.SlowModeWaitError, errors.FloodError),
    (errors.FloodPremiumWaitError, errors.FloodError),
    (errors.TimeoutError, errors.TimedOutError),
)
_CODES = (
    (errors.UnauthorizedError, 401),
    (errors.BadRequestError, 400),
    (errors.AuthKeyError, 406),
    (errors.FloodError, 420),
    (errors.ServerError, 500),
    (errors.TimedOutError, 503),
)


@pytest.mark.parametrize(("child", "parent"), _BASES, ids=lambda c: c.__name__)
def test_the_error_hierarchy_the_ladders_depend_on(child: type, parent: type) -> None:
    assert issubclass(child, parent)
    assert issubclass(child, errors.RPCError)


def test_the_error_codes_and_the_non_rpc_exceptions() -> None:
    for cls, code in _CODES:
        assert cls.code == code
    # ``FloodError`` is not ``FloodWaitError``: the frozen 420 is a sibling, not a child.
    assert not issubclass(errors.FrozenMethodInvalidError, errors.FloodWaitError)
    # A dead-key 406 is NOT unauthorized (401): a ladder on ``UnauthorizedError`` misses it.
    assert not issubclass(errors.AuthKeyDuplicatedError, errors.UnauthorizedError)
    # ``AuthKeyNotFound`` is Telethon's own plain Exception (a transport 404), not an RPCError;
    # ``errors.TimeoutError`` is Telethon's 503 RPCError, not the builtin.
    assert issubclass(errors.AuthKeyNotFound, Exception)
    assert not issubclass(errors.AuthKeyNotFound, errors.RPCError)
    assert errors.TimeoutError is not TimeoutError
    assert not issubclass(errors.TimeoutError, TimeoutError)


def test_flood_wait_carries_seconds() -> None:
    assert errors.FloodWaitError(request=None, capture=42).seconds == 42


_WIRE = (
    (401, "AUTH_KEY_UNREGISTERED", errors.AuthKeyUnregisteredError),
    (401, "AUTH_KEY_INVALID", errors.AuthKeyInvalidError),
    (401, "AUTH_KEY_PERM_EMPTY", errors.AuthKeyPermEmptyError),
    (401, "SESSION_EXPIRED", errors.SessionExpiredError),
    (401, "SESSION_REVOKED", errors.SessionRevokedError),
    (401, "USER_DEACTIVATED", errors.UserDeactivatedError),
    (401, "USER_DEACTIVATED_BAN", errors.UserDeactivatedBanError),
    (401, "SESSION_PASSWORD_NEEDED", errors.SessionPasswordNeededError),
    (406, "AUTH_KEY_DUPLICATED", errors.AuthKeyDuplicatedError),
    (406, "FRESH_RESET_AUTHORISATION_FORBIDDEN", errors.FreshResetAuthorisationForbiddenError),
    (406, "SOMETHING_NOBODY_KNOWS", errors.AuthKeyError),
    (401, "SOMETHING_NOBODY_KNOWS", errors.UnauthorizedError),
    (400, "HASH_INVALID", errors.HashInvalidError),
    (400, "PASSWORD_HASH_INVALID", errors.PasswordHashInvalidError),
    (400, "AUTH_TOKEN_EXPIRED", errors.AuthTokenExpiredError),
    (400, "AUTH_TOKEN_ALREADY_ACCEPTED", errors.AuthTokenAlreadyAcceptedError),
    (420, "FLOOD_WAIT_7", errors.FloodWaitError),
    (420, "FROZEN_METHOD_INVALID", errors.FrozenMethodInvalidError),
)


@pytest.mark.parametrize(("status", "text", "expected"), _WIRE, ids=str)
def test_the_wire_text_maps_to_the_class_we_classify_on(
    status: int, text: str, expected: type
) -> None:
    rpc = types.RpcError(error_code=status, error_message=text)
    error = errors.rpc_message_to_error(rpc, functions.updates.GetStateRequest())
    assert type(error) is expected
    if text == "FLOOD_WAIT_7":
        assert error.seconds == 7


def test_the_dead_key_sets_keep_their_shape() -> None:
    plain = {errors.AuthKeyNotFound}
    for cls in set(_SESSION_ERRORS) - plain:
        assert issubclass(cls, (errors.UnauthorizedError, errors.AuthKeyError)), cls
    assert errors.AuthKeyNotFound in _SESSION_ERRORS
    # The audit's set is the PROVEN subset: no base 406, no transport 404, no 401-only base.
    assert set(_PROVEN_DEAD_KEY) <= set(_SESSION_ERRORS)
    assert not set(_PROVEN_DEAD_KEY) & {errors.AuthKeyError, errors.AuthKeyNotFound}
    # A deactivated account is told apart from a dead key by the 401 base, so the peer-side
    # ``InputUserDeactivatedError`` (a 400) must stay out of "account_deactivated".
    deactivated = {cls for cls, code in _DEAD_SESSION_ERROR_CODES if code == "account_deactivated"}
    assert deactivated == {errors.UserDeactivatedError, errors.UserDeactivatedBanError}
    assert errors.InputUserDeactivatedError in _ACCOUNT_ERRORS
    assert not issubclass(errors.InputUserDeactivatedError, errors.UnauthorizedError)
    dead = {cls for cls, code in _DEAD_SESSION_ERROR_CODES if code == "session_dead"}
    assert dead == set(_SESSION_ERRORS)
    # Every dead-key text Telethon maps lands in the tuple the session check catches.
    for status, text, _ in _WIRE:
        error = errors.rpc_message_to_error(
            types.RpcError(error_code=status, error_message=text),
            functions.updates.GetStateRequest(),
        )
        if text.startswith(("AUTH_KEY_", "SESSION_E", "SESSION_REVOKED")):
            assert isinstance(error, _SESSION_ERRORS), text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "signed_in"),
    [
        (errors.AuthKeyUnregisteredError(request=None), False),
        (errors.SessionRevokedError(request=None), False),
        (errors.UserDeactivatedBanError(request=None), False),
        (errors.AuthKeyDuplicatedError(request=None), "raises"),
        (errors.FloodWaitError(request=None, capture=3), "raises"),
        (ConnectionError("down"), "raises"),
        (None, True),
    ],
    ids=lambda v: type(v).__name__ if isinstance(v, BaseException) else str(v),
)
async def test_probe_authorized_only_reads_unauthorized_as_false(
    error: BaseException | None, signed_in: object
) -> None:
    asked: list[object] = []

    class Client:
        async def __call__(self, request: object) -> None:
            asked.append(request)
            if error is not None:
                raise error

    if signed_in == "raises":
        assert error is not None
        with pytest.raises(type(error)):
            await probe_authorized(Client())  # ty: ignore[invalid-argument-type]
    else:
        assert await probe_authorized(Client()) is signed_in  # ty: ignore[invalid-argument-type]
    assert isinstance(asked[0], functions.updates.GetStateRequest)
