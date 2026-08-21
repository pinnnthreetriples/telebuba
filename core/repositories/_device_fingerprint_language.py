"""The one UPDATE the ``device_fingerprints`` table permits.

Device identity — ``platform``, ``device_model``, ``system_version``,
``app_version`` — is immutable for the account's life: a machine does not change
under its owner, and one that appears to is a linkage signal. The two language
columns are the narrow exception, because a person changing their system
language is unremarkable. That distinction is the whole reason this module
exists as the only write path besides the insert.

It sits beside :mod:`core.repositories.device_fingerprint` rather than inside it
because ``core.repositories._accounts_session_check`` is the caller, and that
module is reached through ``core.db`` before the fingerprint repository is — the
same reason ``_accounts_delete`` and
``_accounts_twofa`` are siblings. It therefore takes the table object from
``core._schema_tables`` directly and imports nothing from ``core.db``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import exists, update

from core._schema_tables import _accounts, _device_fingerprints
from core.device_fingerprint_lang import FALLBACK_TAG, language_pair

if TYPE_CHECKING:
    from sqlalchemy import Update
    from sqlalchemy.engine import Connection


def _language_correction(account_id: str, phone: str | None) -> Update | None:
    """The correction statement for ``phone``, or ``None`` when there is none.

    ``values()`` names ``lang_code`` and ``system_lang_code`` and nothing else,
    so no argument to this function can produce a statement that reaches the
    four device columns.

    ``account_id`` keys the one row; beyond it two predicates bound when the
    correction may fire at all. ``system_lang_code ==
    FALLBACK_TAG`` is the "still the fallback" guard: a row already carrying a
    derived tag matches nothing, and a phone whose own country resolves to the
    fallback anyway (US, or no country at all) yields no statement at all — so a
    fingerprint minted with a genuine ``en-US`` is never rewritten. The EXISTS on
    ``accounts.last_checked_at IS NULL`` is the "first check" guard, and it is
    what keeps rows minted before this correction existed out of scope: those
    drew their two language fields independently and no migration touches them,
    so a legacy ``('ja', 'en-US')`` still looks like a fallback. Rewriting it on
    some routine check months in would change what the account has announced all
    along, which is the linkage signal this correction exists to remove.

    Both live in the WHERE clause rather than in Python so that the guard is
    evaluated where the write happens: two concurrent session checks cannot both
    read an unchecked account on the fallback and both write.
    """
    lang_code, system_lang_code = language_pair(phone)
    if system_lang_code == FALLBACK_TAG:
        return None
    return (
        update(_device_fingerprints)
        .where(
            _device_fingerprints.c.account_id == account_id,
            _device_fingerprints.c.system_lang_code == FALLBACK_TAG,
            exists().where(
                _accounts.c.account_id == account_id,
                _accounts.c.last_checked_at.is_(None),
            ),
        )
        .values(lang_code=lang_code, system_lang_code=system_lang_code)
    )


def _correct_fingerprint_language(
    connection: Connection,
    account_id: str,
    phone: str | None,
) -> None:
    """Apply :func:`_language_correction` on the caller's open connection.

    Taking the connection rather than opening one means the correction commits
    in the same transaction as the phone it was derived from, so the fingerprint
    can never disagree with the ``accounts`` row that produced it.

    The caller must execute this BEFORE it stamps ``accounts.last_checked_at``,
    because that column is the "first check" guard: stamping it first makes the
    EXISTS false and the correction a silent no-op forever.
    """
    statement = _language_correction(account_id, phone)
    if statement is not None:
        connection.execute(statement)
