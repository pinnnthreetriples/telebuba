from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import configure_database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolate_neurocomment_db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")
