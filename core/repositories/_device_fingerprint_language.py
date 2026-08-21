"""The one UPDATE the ``device_fingerprints`` table permits.

Device identity — ``platform``, ``device_model``, ``system_version``,
``app_version`` — is immutable for the account's life: a machine does not change
under its owner, and one that appears to is a linkage signal. The two language
columns are the narrow exception, because a person changing their system
language is unremarkable. That distinction is the whole reason this module
exists as the only write path besides the insert.

It sits beside :mod:`core.repositories.device_fingerprint` (which re-exports it)
rather than inside it because ``core.repositories._accounts_session_check`` is
the caller, and that module is reached through ``core.db`` before the
fingerprint repository is — the same reason ``_accounts_delete`` and
``_accounts_twofa`` are siblings. It therefore takes the table object from
``core._schema_tables`` directly and imports nothing from ``core.db``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import update

from core._schema_tables import _device_fingerprints
from core.device_fingerprint_lang import FALLBACK_TAG, language_pair

if TYPE_CHECKING:
    from sqlalchemy import Update
    from sqlalchemy.engine import Connection


def _language_correction(account_id: str, phone: str | None) -> Update | None:
    """The correction statement for ``phone``, or ``None`` when there is none.

    ``values()`` names ``lang_code`` and ``system_lang_code`` and nothing else,
    so no argument to this function can produce a statement that reaches the
    four device columns.

    The ``system_lang_code == FALLBACK_TAG`` predicate is the "at most once"
    guard. It lives in the WHERE clause, not in Python, so that two concurrent
    session checks cannot both read the fallback and both write. A row already
    carrying a derived tag matches nothing, and a phone whose own country
    resolves to the fallback anyway (US, or no country at all) yields no
    statement at all — so a fingerprint minted with a genuine ``en-US`` is never
    rewritten, and a corrected one is never corrected again.
    """
    lang_code, system_lang_code = language_pair(phone)
    if system_lang_code == FALLBACK_TAG:
        return None
    return (
        update(_device_fingerprints)
        .where(
            _device_fingerprints.c.account_id == account_id,
            _device_fingerprints.c.system_lang_code == FALLBACK_TAG,
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
    """
    statement = _language_correction(account_id, phone)
    if statement is not None:
        connection.execute(statement)
