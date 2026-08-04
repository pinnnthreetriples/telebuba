"""Tests for the warming persistence helpers in ``core.db``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import update

from core.config import settings
from core.db import (
    _get_engine,
    _warming_settings,
    add_warming_channel,
    configure_database,
    create_account,
    fetch_warming_state,
    hand_back_warming_reservation,
    list_warming_channels,
    list_warming_states,
    list_warming_states_by_ids,
    load_warming_settings,
    mark_promoted_to_nc,
    remove_warming_channel,
    save_warming_settings,
    upsert_warming_state,
)
from core.repositories._warming_reservation import _classify_refusal
from schemas.accounts import AccountCreate
from schemas.warming import WarmingStateWrite

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.gemini, "api_key", "")
    monkeypatch.setattr(settings.gemini, "model", "gemini-2.5-flash")


@pytest.mark.asyncio
async def test_add_channel_is_idempotent_and_ordered() -> None:
    await add_warming_channel("@first")
    await add_warming_channel("@second")
    again = await add_warming_channel("@first")

    assert [channel.channel for channel in again.channels] == ["@first", "@second"]


@pytest.mark.asyncio
async def test_remove_channel_drops_the_row() -> None:
    await add_warming_channel("@keep")
    await add_warming_channel("@drop")

    remaining = await remove_warming_channel("@drop")

    assert [channel.channel for channel in remaining.channels] == ["@keep"]


@pytest.mark.asyncio
async def test_list_channels_empty_by_default() -> None:
    channels = await list_warming_channels()

    assert channels.channels == []


@pytest.mark.asyncio
async def test_settings_default_row_is_created_on_first_read() -> None:
    secret = await load_warming_settings()

    assert secret.inter_account_chat is False
    assert secret.reactions_enabled is True
    assert secret.gemini_api_key == ""
    assert secret.gemini_model == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_gemini_tuning_defaults_and_roundtrip() -> None:
    """Retry count + interval default from config and survive a save/reload."""
    secret = await load_warming_settings()
    assert secret.gemini_max_retries == settings.gemini.max_retries
    assert secret.gemini_min_interval_seconds == settings.gemini.min_interval_seconds

    saved = await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=True,
        gemini_api_key=None,
        gemini_max_retries=3,
        gemini_min_interval_seconds=4.5,
    )
    assert saved.gemini_max_retries == 3
    assert saved.gemini_min_interval_seconds == 4.5

    reloaded = await load_warming_settings()
    assert reloaded.gemini_max_retries == 3
    assert reloaded.gemini_min_interval_seconds == 4.5

    # And they KEEP on omission, like every other tuned column: a save that leaves
    # them out (the warming board's config modal, a partial PUT) used to write them
    # unconditionally and reset the settings page's knobs to 1 and 0.0.
    kept = await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=True,
        gemini_api_key=None,
    )
    assert kept.gemini_max_retries == 3
    assert kept.gemini_min_interval_seconds == 4.5


@pytest.mark.asyncio
async def test_gemini_tuning_null_column_falls_back_to_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A legacy row (NULL tuning columns) reads the config defaults, not a crash."""
    monkeypatch.setattr(settings.gemini, "max_retries", 2)
    monkeypatch.setattr(settings.gemini, "min_interval_seconds", 1.5)
    await load_warming_settings()  # ensure the singleton row exists
    with _get_engine().begin() as connection:
        connection.execute(
            update(_warming_settings).values(
                gemini_max_retries=None, gemini_min_interval_seconds=None
            )
        )
    # This raw update bypasses the writer, so the read cache must be dropped by hand.
    from core.repositories._warming_settings import (  # noqa: PLC0415
        _invalidate_warming_settings_cache,
    )

    _invalidate_warming_settings_cache()

    secret = await load_warming_settings()
    assert secret.gemini_max_retries == 2
    assert secret.gemini_min_interval_seconds == 1.5


@pytest.mark.asyncio
async def test_gemini_key_persists_to_db_and_env_is_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A UI-typed key is stored in the DB; a blank column falls back to .env."""
    monkeypatch.setattr(settings.gemini, "api_key", "env-key")

    saved = await save_warming_settings(
        inter_account_chat=True,
        reactions_enabled=False,
        gemini_api_key="ui-typed-key",
    )
    assert saved.gemini_api_key == "ui-typed-key"  # persisted, not ignored
    reloaded = await load_warming_settings()
    assert reloaded.gemini_api_key == "ui-typed-key"

    # Clearing it (empty string) falls back to the .env value on read.
    cleared = await save_warming_settings(
        inter_account_chat=True,
        reactions_enabled=False,
        gemini_api_key="",
    )
    assert cleared.gemini_api_key == "env-key"


@pytest.mark.asyncio
async def test_save_settings_can_clear_key_with_empty_string() -> None:
    await save_warming_settings(
        inter_account_chat=True,
        reactions_enabled=True,
        gemini_api_key="secret-key",
    )

    cleared = await save_warming_settings(
        inter_account_chat=True,
        reactions_enabled=True,
        gemini_api_key="",
    )

    assert cleared.gemini_api_key == ""


@pytest.mark.asyncio
async def test_save_invalidates_the_read_cache() -> None:
    # Prime the in-process cache, then save a new value; the next read must reflect it
    # rather than serving the stale cached row.
    primed = await load_warming_settings()
    assert primed.gemini_api_key == ""  # .env fallback is "" under the fixture

    await save_warming_settings(
        inter_account_chat=True,
        reactions_enabled=True,
        gemini_api_key="rotated-key",
        captcha_llm_provider="openai",
    )

    reloaded = await load_warming_settings()
    assert reloaded.gemini_api_key == "rotated-key"
    assert reloaded.captcha_llm_provider == "openai"


@pytest.mark.asyncio
async def test_list_warming_states_by_ids_scopes_and_guards_empty() -> None:
    for acc in ("acc-1", "acc-2"):
        await create_account(AccountCreate(account_id=acc))
        await upsert_warming_state(WarmingStateWrite(account_id=acc, state="active"))

    scoped = await list_warming_states_by_ids(["acc-1"])

    assert [r.account_id for r in scoped] == ["acc-1"]
    assert await list_warming_states_by_ids([]) == []


@pytest.mark.asyncio
async def test_warming_state_upsert_inserts_then_updates() -> None:
    # Parent account row required now FK is enforced.
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    assert await fetch_warming_state("acc-1") is None

    inserted = await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="active", cycles_completed=0),
    )
    assert inserted.record.state == "active"

    updated = await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            cycles_completed=2,
            last_event="cycle:ok",
        ),
    )

    assert updated.record.state == "sleeping"
    assert updated.record.cycles_completed == 2
    assert updated.record.last_event == "cycle:ok"

    states = await list_warming_states()
    assert [record.account_id for record in states] == ["acc-1"]


@pytest.mark.asyncio
async def test_settings_join_enabled_defaults_on_and_roundtrips() -> None:
    secret = await load_warming_settings()
    assert secret.join_enabled is True

    saved = await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=True,
        join_enabled=False,
        gemini_api_key=None,
    )

    assert saved.join_enabled is False


@pytest.mark.asyncio
async def test_settings_warming_controls_default_and_roundtrip() -> None:
    secret = await load_warming_settings()
    assert secret.enforce_readiness is True

    saved = await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=True,
        enforce_readiness=False,
        gemini_api_key=None,
    )

    assert saved.enforce_readiness is False


@pytest.mark.asyncio
async def test_warming_state_persists_proxy_snapshot_and_daily_fields() -> None:
    await create_account(AccountCreate(account_id="acc-1"))

    result = await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            proxy_snapshot="socks5://1.2.3.4:1080",
            daily_actions=7,
            daily_count_date="2026-06-12",
        ),
    )

    assert result.record.proxy_snapshot == "socks5://1.2.3.4:1080"
    assert result.record.daily_actions == 7
    assert result.record.daily_count_date == "2026-06-12"

    again = await fetch_warming_state("acc-1")
    assert again is not None
    assert again.daily_actions == 7


@pytest.mark.asyncio
async def test_mark_promoted_to_nc_rejects_unknown_account() -> None:
    """Bug 14: upserting promoted_to_nc for a non-existent account would create a ghost row."""
    with pytest.raises(ValueError, match="unknown account_id"):
        await mark_promoted_to_nc("does-not-exist")

    # And no warming-state ghost row was left behind.
    assert await fetch_warming_state("does-not-exist") is None


# #10: the hand-back's refusal ladder, stated as a table so the ORDER of its checks and
# the boundary of its comparison are pinned, not just their presence. Rows are
# ``(row_token, row_count, row_date_is_ours)`` against a booking of ``_BOOKED`` on
# ``_DATE``; both were mutations that survived the behavioural tests.
_TOKEN = "booking-1"
_BOOKED = 15
_DATE = "2026-08-04"
_OTHER_DATE = "2026-08-05"


@pytest.mark.parametrize(
    ("row_token", "row_count", "row_date", "expected"),
    [
        # Our own applying write cleared it, or the row never had a booking at all.
        (None, _BOOKED, _DATE, "settled"),
        # A newer booking holds the row: our remainder is inside its baseline.
        ("booking-2", _BOOKED, _DATE, "absorbed"),
        # A rolled date is answered BEFORE the count is looked at: the count belongs to
        # another day, so "grown past our booking" says nothing about our budget. Both
        # sides of the boundary, so swapping the two checks fails here.
        (_TOKEN, _BOOKED + 5, _OTHER_DATE, "settled"),
        (_TOKEN, 0, _OTHER_DATE, "settled"),
        # Our date: strictly below what we booked is already released.
        (_TOKEN, _BOOKED - 1, _DATE, "settled"),
        # At or above it, the reservation is still counted with nobody to release it.
        # Equality is unreachable in production (the UPDATE would have matched), so it is
        # pinned here instead — relaxing ``>=`` to ``>`` fails on this row alone.
        (_TOKEN, _BOOKED, _DATE, "stranded"),
        (_TOKEN, _BOOKED + 5, _DATE, "stranded"),
    ],
)
def test_the_refusal_ladder_answers_every_row_state(
    row_token: str | None,
    row_count: int,
    row_date: str,
    expected: str,
) -> None:
    row = {
        "reservation_token": row_token,
        "daily_actions": row_count,
        "daily_count_date": row_date,
    }

    verdict = _classify_refusal(row, token=_TOKEN, booked=_BOOKED, daily_date=_DATE)

    assert verdict == expected


@pytest.mark.asyncio
async def test_a_hand_back_for_a_purged_row_owes_nobody_anything() -> None:
    """``remove_account`` can delete the row before the hand-back reaches it (#10)."""
    outcome = await hand_back_warming_reservation(
        "does-not-exist", token=_TOKEN, booked=_BOOKED, reconciled=2, daily_date=_DATE
    )

    assert outcome == "settled"
