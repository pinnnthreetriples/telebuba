"""Extras contract — registry keys, the toggle TypedDict and its defaults stay one set."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas._warming_extras import EXTRA_TOGGLE_DEFAULTS, ExtraToggles
from schemas.warming import WarmingSettings, WarmingSettingsUpdate
from services.warming._extras import EXTRAS


def test_registry_keys_are_toggle_keys_with_a_default_each() -> None:
    toggle_keys = set(ExtraToggles.__annotations__)
    assert toggle_keys == EXTRA_TOGGLE_DEFAULTS.keys()
    # ponytail: becomes == when PR5 lands and every toggle has a registered spec.
    assert {spec.key for spec in EXTRAS} <= toggle_keys
    assert len({spec.key for spec in EXTRAS}) == len(EXTRAS)


def test_write_specs_are_exactly_the_self_scoped_saved_messages_keys() -> None:
    # ponytail: grows with PR4 (polls, leave, archive, mute) and PR5 (video, voice, emoji_status).
    assert {spec.key for spec in EXTRAS if spec.kind == "write"} <= {
        "saved",
        "scheduled",
        "drafts",
        "forward",
    }


def test_update_rejects_an_unknown_toggle_and_accepts_none() -> None:
    with pytest.raises(ValidationError):
        WarmingSettingsUpdate.model_validate({"extra_toggles": {"nope": True}})
    assert WarmingSettingsUpdate(extra_toggles=None).extra_toggles is None
    assert WarmingSettingsUpdate().extra_toggles is None
    assert WarmingSettingsUpdate(extra_toggles={"polls": True}).extra_toggles == {"polls": True}


def test_read_model_defaults_to_the_full_key_set() -> None:
    model = WarmingSettings(gemini_model="m", updated_at="now")
    assert model.extra_toggles == EXTRA_TOGGLE_DEFAULTS
    # A fresh copy per instance — mutating one model must not leak into the defaults.
    model.extra_toggles["polls"] = True
    assert EXTRA_TOGGLE_DEFAULTS["polls"] is False
