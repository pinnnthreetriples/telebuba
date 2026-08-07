"""Captcha give-up migration body — a sibling of ``core.migration_steps_unconfirmed_ban``.

Its own module for the reason those have: the neurocomment migration modules are at the
file-size cap. Idempotent, per the append-only migration contract in ``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_readiness_captcha_giveup(connection: Connection) -> None:
    # #49: a pair the guardian bot's captcha blocks carries the readiness triple
    # ``(joined=1, captcha_passed=0, ready=0)``, which matches none of onboarding's
    # guards — so every trigger re-ran the solver on it, forever, with nothing counting
    # the failures and nothing ever ending them. These two columns are that budget and
    # its terminal state: ``captcha_retry_at`` is when the sweep authorised the ONE
    # re-solve this rule grants (NULL = not asked yet), and ``captcha_gave_up`` says the
    # pair stopped trying and left the discussion chat for good. NOT the #43 re-join
    # pair — that one counts attempts to get back INTO a chat we were thrown out of,
    # while this pair is a member the bot will not let speak. NULL / 0 on existing rows
    # = nothing asked and nobody terminal, which is the right starting point: nobody may
    # be given up on for evidence gathered before the column existed.
    columns = _sqlite_columns(connection, "neurocomment_readiness")
    if "captcha_retry_at" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_readiness ADD COLUMN captcha_retry_at VARCHAR",
        )
    if "captcha_gave_up" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_readiness "
            "ADD COLUMN captcha_gave_up INTEGER NOT NULL DEFAULT 0",
        )
