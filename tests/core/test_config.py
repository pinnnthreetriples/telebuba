"""Config validation for ``NeurocommentSettings`` — cross-field min≤max bounds."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config import NeurocommentSettings


def test_reply_delay_min_must_not_exceed_max() -> None:
    with pytest.raises(ValidationError):
        NeurocommentSettings(reply_delay_min_seconds=10.0, reply_delay_max_seconds=3.0)


def test_join_delay_min_must_not_exceed_max() -> None:
    with pytest.raises(ValidationError):
        NeurocommentSettings(join_delay_min_seconds=60.0, join_delay_max_seconds=30.0)
