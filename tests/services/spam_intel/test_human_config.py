"""The Stage 3 switches: off by default, mirrored in ``.env.example``, validated like the rest."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from core._config_spam_intel import SpamIntelSettings

_ENV_EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"
_RANGES = (
    "human_pre_open",
    "human_open_to_typing",
    "human_typing",
    "human_quiet",
    "human_read_delay",
    "live_session_delay",
)


def test_the_switches_default_off() -> None:
    cfg = SpamIntelSettings()
    assert (cfg.human_sequence, cfg.live_session_embedding) == (False, False)


@pytest.mark.parametrize("name", _RANGES)
def test_a_minimum_above_its_maximum_is_refused(name: str) -> None:
    with pytest.raises(ValidationError, match=f"{name}_min_seconds must not exceed"):
        SpamIntelSettings.model_validate({f"{name}_min_seconds": 9.0, f"{name}_max_seconds": 1.0})


@pytest.mark.parametrize("name", _RANGES)
def test_an_equal_minimum_and_maximum_is_fine(name: str) -> None:
    cfg = SpamIntelSettings.model_validate({f"{name}_min_seconds": 3.0, f"{name}_max_seconds": 3.0})
    assert getattr(cfg, f"{name}_min_seconds") == getattr(cfg, f"{name}_max_seconds")


@pytest.mark.parametrize(
    "field",
    [
        "human_reply_wait_seconds",
        "human_quiet_min_seconds",
        "human_quiet_max_seconds",
        "live_session_retry_minutes",
    ],
)
def test_a_wait_that_must_be_positive_is_refused_at_zero(field: str) -> None:
    with pytest.raises(ValidationError):
        SpamIntelSettings.model_validate({field: 0.0})


def test_a_negative_pause_is_refused() -> None:
    with pytest.raises(ValidationError):
        SpamIntelSettings(human_pre_open_min_seconds=-1.0)


def test_every_new_setting_is_mirrored_in_env_example() -> None:
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    declared = {
        line.split("=", 1)[0]
        for line in text.splitlines()
        if line.startswith("SPAM_INTEL__") and "=" in line
    }
    new = [
        name
        for name in SpamIntelSettings.model_fields
        if name.startswith(("human_", "live_session_"))
    ]
    assert new
    assert {f"SPAM_INTEL__{name.upper()}" for name in new} <= declared


def test_the_env_example_values_are_the_defaults() -> None:
    values = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line.startswith(("SPAM_INTEL__HUMAN_", "SPAM_INTEL__LIVE_SESSION_"))
    }
    cfg = SpamIntelSettings.model_validate(
        {key.removeprefix("SPAM_INTEL__").lower(): value for key, value in values.items()}
    )
    assert cfg == SpamIntelSettings()
