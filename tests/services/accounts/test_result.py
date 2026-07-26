"""``raise_for_result``'s code contract: bounded and locale-neutral, always.

``AccountActionError.code`` is what the SPA translates (non-negotiable #12), so
it must never be a third-party exception message. These tests pin both halves:
our gateway codes survive verbatim, and everything else collapses to the
``ActionStatus``.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys

import pytest
from PIL import UnidentifiedImageError
from telethon import errors

from schemas.telegram_actions import ActionResult
from services.accounts._result import (
    _STABLE_CODE_ERROR_TYPES,
    AccountActionError,
    raise_for_result,
)


def _failed_result(*, error_type: str, error_message: str) -> ActionResult:
    """The ``failed`` result ``core.telegram_client._generic_error`` would build."""
    return ActionResult(
        status="failed",
        action_type="set_profile_photo",
        account_id="acc-1",
        error_type=error_type,
        error_message=error_message,
    )


@pytest.mark.parametrize(
    "exc",
    [
        errors.rpcerrorlist.PhotoCropSizeSmallError(None),
        UnidentifiedImageError("cannot identify image file"),
        ValueError("No linked discussion group for '@somechannel'"),
        RuntimeError("database is locked"),
    ],
    ids=["telethon_rpc", "pillow", "unmapped_value_error", "runtime"],
)
def test_third_party_message_never_becomes_the_code(exc: Exception) -> None:
    """Raw English prose is not a contract, so it must not cross the API boundary.

    ``_generic_error`` sets ``error_message`` to ``str(exc)`` for every unmapped
    exception. The prose still reaches the failure log; only the code the
    envelope carries collapses to the bounded status the SPA can act on.
    """
    result = _failed_result(error_type=type(exc).__name__, error_message=str(exc))

    with pytest.raises(AccountActionError) as excinfo:
        raise_for_result(result)

    assert excinfo.value.code == "failed"
    assert str(exc) not in str(excinfo.value)


@pytest.mark.parametrize(
    ("error_type", "code"),
    [
        ("ProfileGatewayError", "username_occupied"),
        ("ChannelGatewayError", "channel_username_occupied"),
        ("StoryImageNormalisationError", "story_image_invalid"),
        ("StoryCollageLayoutError", "story_collage_unknown_layout"),
        ("StoryVideoNormalisationError", "story_video_invalid"),
    ],
)
def test_gateway_stable_code_survives_verbatim(error_type: str, code: str) -> None:
    """Our gateway errors ARE constructed with the code, so the message is the code."""
    with pytest.raises(AccountActionError) as excinfo:
        raise_for_result(_failed_result(error_type=error_type, error_message=code))

    assert excinfo.value.code == code


def _gateway_value_error_names() -> set[str]:
    """Every ``ValueError`` subclass DEFINED in a ``core.telegram_client`` module."""
    package = importlib.import_module("core.telegram_client")
    for module in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{package.__name__}.{module.name}")
    names: set[str] = set()
    for module_name, module in list(sys.modules.items()):
        if module is None or not module_name.startswith("core.telegram_client"):
            continue
        for obj in vars(module).values():
            if not inspect.isclass(obj) or obj.__module__ != module_name:
                continue
            if issubclass(obj, ValueError):
                names.add(obj.__name__)
    return names


def test_every_gateway_value_error_is_allowlisted() -> None:
    """Tripwire: the allowlist is pinned by NAME, so it cannot follow a rename itself.

    Every ``ValueError`` the gateway raises today is a stable-code error. A new
    one that carries prose instead must fail here and be dealt with
    deliberately — silently inheriting "the message is the code" is exactly the
    contract break this allowlist exists to stop.
    """
    assert _gateway_value_error_names() == _STABLE_CODE_ERROR_TYPES
