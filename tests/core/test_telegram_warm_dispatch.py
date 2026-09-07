"""Routing and contract tests for the ``warm_*`` action family.

Covers the prefix table ``_dispatch_action`` shares with the channel family, the
unhandled-model guard, and the policy contracts the plan pins for every warming read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, get_args

import pytest
from pydantic import BaseModel, TypeAdapter

from core.telegram_client._action_families import prefix_family
from core.telegram_client._channels import _dispatch_channel_action
from core.telegram_client._profile import _PROFILE_EDIT_ACTION_TYPES
from core.telegram_client._warm_dispatch import dispatch_warming_action, warm_log_extra
from schemas.telegram_actions import TelegramAction
from schemas.telegram_actions_warming import (
    WarmCheckSettings,
    WarmGetDialogs,
    WarmingAction,
    WarmReadContacts,
    WarmViewProfile,
)

_GATEWAY_DIR = Path(__file__).resolve().parents[2] / "core" / "telegram_client"
# Contact-import verbs the operator ruled out for warming; the sync is read-only.
_FORBIDDEN_REQUESTS = ("ImportContactsRequest", "AddContactRequest", "ResolvePhoneRequest")
_WARMING_MODELS = get_args(get_args(WarmingAction)[0])


class _WarmNope(BaseModel):
    action_type: Literal["warm_nope"] = "warm_nope"


def test_prefix_family_routes_warm_and_channel_only() -> None:
    warm = prefix_family("warm_x")
    channel = prefix_family("channel_x")
    assert warm is not None
    assert warm.dispatch is dispatch_warming_action
    assert warm.log_extra is warm_log_extra
    assert channel is not None
    assert channel.dispatch is _dispatch_channel_action
    assert prefix_family("join_channel") is None


@pytest.mark.asyncio
async def test_unhandled_warm_model_raises_instead_of_falling_through() -> None:
    with pytest.raises(ValueError, match="warm_nope"):
        await dispatch_warming_action(object(), _WarmNope())  # ty: ignore[invalid-argument-type]


def test_no_warm_type_is_a_sticky_profile_edit() -> None:
    assert not {t for t in _PROFILE_EDIT_ACTION_TYPES if t.startswith("warm_")}


@pytest.mark.parametrize("model", _WARMING_MODELS, ids=lambda m: m.__name__)
def test_every_warming_model_is_a_telegram_action(model: type[BaseModel]) -> None:
    validated = TypeAdapter(TelegramAction).validate_python(model().model_dump())
    assert type(validated) is model
    assert validated.action_type.startswith("warm_")


@pytest.mark.parametrize(
    "path",
    sorted(_GATEWAY_DIR.glob("_warm_*.py")),
    ids=lambda p: p.name,
)
def test_warm_modules_never_import_contact_writes(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    assert not [name for name in _FORBIDDEN_REQUESTS if name in source]


def test_warm_log_extra_carries_only_counts_kinds_and_handles() -> None:
    assert warm_log_extra(WarmGetDialogs(limit=5)) == {"limit": 5}
    assert warm_log_extra(WarmCheckSettings(calls=2)) == {}  # the dispatcher logs ``calls``
    assert warm_log_extra(WarmReadContacts()) == {}
    assert warm_log_extra(WarmViewProfile()) == {"kind": "self", "channel": None}
    assert warm_log_extra(WarmViewProfile(kind="channel", channel="@x")) == {
        "kind": "channel",
        "channel": "@x",
    }
