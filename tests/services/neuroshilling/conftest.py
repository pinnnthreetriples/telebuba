"""Local fixtures for neuroshilling service tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import configure_database
from services import _account_owner

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolate_neuroshilling(tmp_path: Path) -> Iterator[None]:
    """A fresh database and an empty ownership registry for every test.

    Both are process-global, and the registry decides what the board reports as
    busy — a claim left behind by an earlier test would make the next one's
    verdict depend on execution order. The pacer is reset suite-wide by the root
    conftest, so it is not repeated here.
    """
    configure_database(tmp_path / "telebuba.db")
    _account_owner.reset_for_tests()
    yield
    _account_owner.reset_for_tests()
