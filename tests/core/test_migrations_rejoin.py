"""Migration bodies for the re-join rule's columns.

Own module because ``test_migrations`` sits at the 700-line test cap, which is also why
``core.migration_steps_rejoin`` is its own module on the other side.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import configure_database  # type: ignore[attr-defined]

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Connection

    from tests.core.conftest import _EngineFactory


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


def _legacy_readiness(connection: Connection) -> None:
    """The pre-#43 table: no re-join columns at all, one parked pair in it."""
    connection.exec_driver_sql(
        "CREATE TABLE neurocomment_readiness ("
        "account_id VARCHAR NOT NULL, channel VARCHAR NOT NULL, joined INTEGER NOT NULL, "
        "captcha_passed INTEGER NOT NULL, ready INTEGER NOT NULL, "
        "checked_at VARCHAR NOT NULL, PRIMARY KEY (account_id, channel))",
    )
    connection.exec_driver_sql(
        "INSERT INTO neurocomment_readiness VALUES ('a1', '@news', 0, 1, 0, '2026-01-01')",
    )


def test_readiness_rejoin_gave_up_added_and_defaults_to_unreported(
    legacy_engine: _EngineFactory,
) -> None:
    """#50: the mark that keeps a spent re-join budget from being reported every tick.

    0 on every existing row, so a pair that had already given up before the upgrade gets
    its line (and its leave) on the first sweep after it — the state it should have had
    all along. A default of 1 would silence exactly the pairs this rule exists for.
    """
    from core.migration_steps_rejoin import (  # noqa: PLC0415
        _add_readiness_rejoin,
        _add_readiness_rejoin_gave_up,
    )

    engine = legacy_engine("legacy-gave-up.db")
    with engine.begin() as connection:
        _legacy_readiness(connection)
        _add_readiness_rejoin(connection)
        _add_readiness_rejoin_gave_up(connection)
        _add_readiness_rejoin_gave_up(connection)  # idempotent — must not raise.

    with engine.connect() as connection:
        gave_up = connection.exec_driver_sql(
            "SELECT rejoin_gave_up FROM neurocomment_readiness",
        ).scalar_one()
    assert int(gave_up) == 0
