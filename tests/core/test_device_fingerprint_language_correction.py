"""The single language correction allowed after a fingerprint is minted.

Both import paths build their ``AccountCreate`` with no phone — the connection
that learns the number needs the fingerprint first — so they mint the ``en-US``
fallback and would otherwise announce it for the account's life. The first
successful session check is where the phone arrives, and that is the only moment
this correction may fire.

Two of the cases below assert on generated SQL rather than on an outcome, which
is why: `core/` is outside the mutation sweep (`pyproject.toml` `source_paths` is
`["services", "schemas"]`), so nothing but an explicit test pins these
predicates. Deleting the UPDATE's `account_id` predicate once left the entire
suite green while making one check rewrite every fallback row in the table.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from core.db import (
    _device_fingerprints,
    _get_engine,
    configure_database,
    create_account,
    fetch_device_fingerprint,
    insert_device_fingerprint,
    update_account_from_session_check,
    update_account_status,
)
from core.device_fingerprint import get_or_create_device_fingerprint
from core.repositories._device_fingerprint_language import _language_correction
from schemas.telegram_session import TelegramSessionCheckResult
from tests.factories import AccountCreateFactory, DeviceFingerprintFactory

if TYPE_CHECKING:
    from pathlib import Path

_DEVICE_COLUMNS = ("platform", "device_model", "system_version", "app_version")


def _alive(account_id: str, phone: str | None) -> TelegramSessionCheckResult:
    return TelegramSessionCheckResult(
        account_id=account_id,
        session_path=f"sessions/{account_id}",
        status="alive",
        is_temporary=False,
        user_id=1,
        phone=phone,
    )


async def _seed(tmp_path: Path, account_id: str, **fingerprint: str) -> None:
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreateFactory.build(account_id=account_id))
    await insert_device_fingerprint(
        DeviceFingerprintFactory.build(account_id=account_id, **fingerprint),
    )


def test_the_update_statement_can_only_reach_the_two_language_columns() -> None:
    """Structural, not behavioural: read the SET clause the table will receive.

    The narrowed invariant is only worth anything if it is impossible to widen by
    accident, so this asserts on the generated SQL rather than on the outcome of
    one call.
    """
    statement = _language_correction("acc", "+79161234567")
    assert statement is not None
    set_clause = re.search(r"\bSET (.*?) WHERE ", str(statement), re.DOTALL)
    assert set_clause is not None
    assigned = {part.split("=")[0].strip() for part in set_clause.group(1).split(",")}

    assert assigned == {"lang_code", "system_lang_code"}


def test_the_update_reaches_one_account_and_only_before_its_first_check() -> None:
    """Structural, like the SET-clause test above, and for the same reason.

    That test bounds which columns a correction may touch; this one bounds which
    rows. Both halves of "one account, once, and only on its first check" live in
    the WHERE clause and nowhere else, so dropping a predicate has to fail here.
    Behaviourally the ``account_id`` half is invisible to any single-account test,
    and its absence would give every fallback row in the table one shared
    language — the linkage signal this correction exists to remove.
    """
    statement = _language_correction("acc", "+79161234567")
    assert statement is not None
    where_clause = str(statement).split(" WHERE ", 1)[1]
    constrained = set(re.findall(r"\b(?:device_fingerprints|accounts)\.\w+", where_clause))

    assert constrained == {
        "device_fingerprints.account_id",
        "device_fingerprints.system_lang_code",
        "accounts.account_id",
        "accounts.last_checked_at",
    }


def test_no_statement_at_all_without_a_country_to_correct_to() -> None:
    assert _language_correction("acc", None) is None
    assert _language_correction("acc", "8 916 123 45 67") is None


@pytest.mark.asyncio
async def test_first_session_check_corrects_an_imported_fallback(tmp_path: Path) -> None:
    await _seed(tmp_path, "imported")

    await update_account_from_session_check(_alive("imported", "+79161234567"))
    corrected = await fetch_device_fingerprint("imported")

    assert corrected is not None
    assert (corrected.lang_code, corrected.system_lang_code) == ("ru", "ru-RU")


@pytest.mark.asyncio
async def test_correction_leaves_every_device_column_byte_identical(tmp_path: Path) -> None:
    """Immutability still holds for the four columns the exception excludes."""
    await _seed(tmp_path, "device")
    before = await fetch_device_fingerprint("device")
    assert before is not None

    await update_account_from_session_check(_alive("device", "+819012345678"))
    after = await fetch_device_fingerprint("device")

    assert after is not None
    assert after.system_lang_code == "ja-JP"
    for column in _DEVICE_COLUMNS:
        assert getattr(after, column) == getattr(before, column)


@pytest.mark.asyncio
async def test_correction_fires_at_most_once(tmp_path: Path) -> None:
    """A second check reporting a different country must not move the language.

    Once corrected the row no longer matches the WHERE clause, so the account
    keeps the language it settled on rather than following every re-check.
    """
    await _seed(tmp_path, "twice")

    await update_account_from_session_check(_alive("twice", "+79161234567"))
    await update_account_from_session_check(_alive("twice", "+4915112345678"))
    final = await fetch_device_fingerprint("twice")

    assert final is not None
    assert (final.lang_code, final.system_lang_code) == ("ru", "ru-RU")


@pytest.mark.asyncio
async def test_an_already_derived_language_is_never_corrected(tmp_path: Path) -> None:
    """The guard is "still the fallback", not "differs from the phone"."""
    await _seed(tmp_path, "derived", lang_code="de", system_lang_code="de-DE")

    await update_account_from_session_check(_alive("derived", "+79161234567"))
    unchanged = await fetch_device_fingerprint("derived")

    assert unchanged is not None
    assert (unchanged.lang_code, unchanged.system_lang_code) == ("de", "de-DE")


@pytest.mark.asyncio
async def test_a_check_that_learned_no_phone_corrects_nothing(tmp_path: Path) -> None:
    """A non-alive check writes no phone, so it must not touch the language."""
    await _seed(tmp_path, "dead")

    await update_account_from_session_check(
        _alive("dead", "+79161234567").model_copy(update={"status": "unauthorized"}),
    )
    unchanged = await fetch_device_fingerprint("dead")

    assert unchanged is not None
    assert unchanged.system_lang_code == "en-US"


@pytest.mark.asyncio
async def test_the_correction_writes_no_row_when_no_fingerprint_exists(tmp_path: Path) -> None:
    """``add_account`` mints the fingerprint, but the ordering is not guaranteed.

    The UPDATE is keyed on ``account_id``, so a check that arrives first is a
    no-op rather than an insert of a language with no device attached to it.
    """
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreateFactory.build(account_id="unminted"))

    await update_account_from_session_check(_alive("unminted", "+4915112345678"))

    with _get_engine().connect() as connection:
        rows = connection.execute(select(_device_fingerprints)).mappings().all()
    assert rows == []


@pytest.mark.asyncio
async def test_a_later_mint_still_reads_the_learned_phone(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreateFactory.build(account_id="late"))
    await update_account_from_session_check(_alive("late", "+4915112345678"))

    minted = await get_or_create_device_fingerprint("late")

    assert (minted.lang_code, minted.system_lang_code) == ("de", "de-DE")


@pytest.mark.asyncio
async def test_correction_repairs_an_operator_typed_number(tmp_path: Path) -> None:
    """The residual of the unvalidated phone field, closed on the first connect.

    ``services.accounts.login`` stores whatever the operator typed, and
    ``'8 916 123 45 67'`` has no country, so the mint falls back. Telegram then
    returns ``me.phone`` as bare canonical digits and the correction lands —
    without this module parsing or rewriting the operator's string anywhere.
    """
    configure_database(tmp_path / "telebuba.db")
    await create_account(
        AccountCreateFactory.build(account_id="typed", phone="8 916 123 45 67"),
    )
    minted = await get_or_create_device_fingerprint("typed")
    assert minted.system_lang_code == "en-US"

    await update_account_from_session_check(_alive("typed", "79161234567"))
    corrected = await fetch_device_fingerprint("typed")

    assert corrected is not None
    assert (corrected.lang_code, corrected.system_lang_code) == ("ru", "ru-RU")


@pytest.mark.asyncio
async def test_correcting_one_account_leaves_a_neighbour_byte_identical(tmp_path: Path) -> None:
    """One check must move one row, whatever else is on the fallback beside it.

    Every other case here uses a single account, which is the one shape that
    cannot observe an unkeyed UPDATE: a fleet is born on the same ``en-US``
    fallback, so without the ``account_id`` predicate one alive check rewrites
    every fingerprint in the table to the checked account's language.
    """
    await _seed(tmp_path, "checked")
    await create_account(AccountCreateFactory.build(account_id="neighbour"))
    await insert_device_fingerprint(DeviceFingerprintFactory.build(account_id="neighbour"))
    before = await fetch_device_fingerprint("neighbour")
    assert before is not None

    await update_account_from_session_check(_alive("checked", "+79161234567"))

    checked = await fetch_device_fingerprint("checked")
    neighbour = await fetch_device_fingerprint("neighbour")
    assert checked is not None
    assert (checked.lang_code, checked.system_lang_code) == ("ru", "ru-RU")
    assert neighbour == before


@pytest.mark.asyncio
async def test_only_an_account_never_checked_before_is_corrected(tmp_path: Path) -> None:
    """The fallback guard alone would rewrite rows minted before this correction.

    Pre-existing fingerprints drew the two language fields independently and no
    migration touches them, so a legacy ``('ja', 'en-US')`` still matches "still
    the fallback" and would be rewritten on its next routine check — months into
    the account's life, changing what it has announced all along. ``accounts``
    knows the difference: ``last_checked_at`` is NULL only until something has
    checked the account, so the correction is confined to a first check.
    """
    await _seed(tmp_path, "legacy", lang_code="ja")
    await update_account_status("legacy", status="alive")
    await create_account(AccountCreateFactory.build(account_id="fresh"))
    await insert_device_fingerprint(DeviceFingerprintFactory.build(account_id="fresh"))

    await update_account_from_session_check(_alive("legacy", "+79161234567"))
    await update_account_from_session_check(_alive("fresh", "+79161234567"))

    legacy = await fetch_device_fingerprint("legacy")
    fresh = await fetch_device_fingerprint("fresh")
    assert legacy is not None
    assert fresh is not None
    assert (legacy.lang_code, legacy.system_lang_code) == ("ja", "en-US")
    assert (fresh.lang_code, fresh.system_lang_code) == ("ru", "ru-RU")
