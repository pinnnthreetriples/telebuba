"""One-shot retry-budget reset for the 4 → 2 change — a sibling of ``core.migration_steps``.

Its own module for the reason its siblings have one: the neurocomment migration modules
are at the file-size cap. Idempotent, per the append-only migration contract in
``core.migrations``.

THIS STEP IS ONE-SHOT AND TIED TO ONE SPECIFIC SETTING CHANGE — do NOT copy it as a
general technique. It exists because ``channel_max_rounds`` dropped from 4 to 2 on a
database whose rows had already been counted against the OLD budget, and neither rule
that reads those counters has a migration path for a budget that shrinks under them. It
hard-codes the new budget rather than reading ``settings``: a migration must mean the same
thing on every future run, and a step that re-reads live configuration would re-fire —
and wrongly zero legitimately earned counters — the next time an operator retunes the
setting. The general answer to a stale deadline is the freshness check each rule carries on
its own: ``_channel_pause._window_stale`` for the pause window,
``services.neurocomment._rejoin._stamp_stale`` for the per-pair stamp. Both refuse a verdict
read off a row that has been lying around for a whole extra window, which is what covers a
LATER change to these settings — for a row nobody was posting or re-joining against. Neither
covers the knife-edge: a budget shrunk while a window had only just run out is still judged
on the spot, so a change like this one still needs its own step. This step cleans up the one
that already happened.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

# The budget as of this migration. A literal, not ``settings.neurocomment.channel_max_
# rounds``: see the module docstring — re-reading the live setting would make this step
# mean something different on every future run.
_NEW_MAX_ROUNDS = 2


def _reset_overshot_retry_budgets(connection: Connection) -> None:
    # #48: ``channel_max_rounds`` went 4 → 2, and both multi-day rules that spend it read
    # their counters straight off these rows. Nothing bridged the change, so on the first
    # tick after the upgrade every row that had reached 2 under the old budget of 4 was
    # already "exhausted" — and because its timestamp was more than a window old, the
    # deferred verdict came due in the same tick. Live DB at the time of writing: 23
    # readiness rows across six channels (sportexpress, laquintacolumna,
    # binance_announcements, kinopoisk, okkosport, LEARN_ENGLISH_LANGUAGE_USA) would have
    # been unlinked on the first sweep after deploy, without a single re-join under the
    # new rule and without the 48h the rule promises.
    #
    # Zeroing counter AND timestamp together is the whole point: a counter alone would
    # leave a stale deadline that reads as "this window already ran out". Rows still
    # BELOW the new budget are left exactly as they are — they have attempts left and
    # their timeline is still honest under the new rule. The rule changed, so everyone it
    # had already spent starts over with the full new budget.
    _reset_readiness_rejoin(connection)
    _reset_channel_pause_rounds(connection)


def _reset_readiness_rejoin(connection: Connection) -> None:
    # The ``_rejoin`` side: a pair parked out of a discussion group. Guarded on the #43
    # columns actually existing, so the step is inert on a database that somehow never got
    # them rather than raising mid-upgrade.
    columns = _sqlite_columns(connection, "neurocomment_readiness")
    if not {"rejoin_attempts", "rejoin_attempted_at"} <= columns:
        return
    connection.exec_driver_sql(
        "UPDATE neurocomment_readiness "
        "SET rejoin_attempts = 0, rejoin_attempted_at = NULL "
        "WHERE rejoin_attempts >= ?",
        (_NEW_MAX_ROUNDS,),
    )


def _reset_channel_pause_rounds(connection: Connection) -> None:
    # The ``_channel_pause`` side: a channel that refused writes. The live database has no
    # row over the new budget here, but the same 4 → 2 hole is open — ``paused_until`` is
    # never cleared by the window merely elapsing, so a week-old deadline sitting beside
    # ``pause_rounds >= 2`` is judged by the new budget the moment the sweep sees it.
    # ``neurocomment_campaign_channels`` is outside ``migration_steps._ALLOWED_TABLES``, so
    # the column probe is inlined exactly as migration #42 does it (a hard-coded table
    # name, never user input).
    if not _sqlite_table_exists(connection, "neurocomment_campaign_channels"):
        return
    rows = (
        connection.exec_driver_sql("PRAGMA table_info(neurocomment_campaign_channels)")
        .mappings()
        .all()
    )
    if not {"pause_rounds", "paused_until"} <= {str(row["name"]) for row in rows}:
        return
    connection.exec_driver_sql(
        "UPDATE neurocomment_campaign_channels "
        "SET pause_rounds = 0, paused_until = NULL "
        "WHERE pause_rounds >= ?",
        (_NEW_MAX_ROUNDS,),
    )
