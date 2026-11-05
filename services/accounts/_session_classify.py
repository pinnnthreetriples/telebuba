"""Reading the session list the way the operator needs it: whose it is, and how old its key is.

Two facts the gateway cannot know. Telebuba's own Telegram Web login is an authorization of
the account that is not one of its Telethon devices; it is recorded when the login ends
(:mod:`.own_authorizations`) and must count as ours, or the card calls it foreign and the
close would end it. And a session of ours whose key was created BEFORE the account was added
to Telebuba was made by the seller, who very likely still holds a copy: the device name on
that row is the fingerprint Telebuba presents, but the date is the key's own.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from schemas.session_audit import AccountSession, AccountSessionAudit
    from schemas.session_rekey import StoredRekey


def parse_moment(value: str | None) -> datetime | None:
    """An ISO timestamp as an aware UTC datetime; ``None`` when absent or unreadable."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def mark_own(sessions: list[AccountSession], own: Mapping[str, str]) -> list[AccountSession]:
    """``sessions`` with every recorded own authorization marked ours (kind ``web``)."""
    marked: list[AccountSession] = []
    for session in sessions:
        kind = own.get(session.hash)
        if kind == "web" and not session.is_ours:
            session = session.model_copy(update={"is_ours": True, "kind": "web"})  # noqa: PLW2901
        marked.append(session)
    return marked


def _with_predates(session: AccountSession, *, predates: bool) -> AccountSession:
    flagged = session.model_copy()
    flagged.key_predates_import = predates
    return flagged


def flag_seller_keys(
    sessions: list[AccountSession], imported_at: str, rekey: StoredRekey | None
) -> list[AccountSession]:
    """Flag our Telethon sessions whose key predates the account's import into Telebuba.

    After a re-key the key must also predate the re-key itself: a seller key that is still
    listed (the old authorization is waiting to be ended) is older than both, while the key
    the re-key made is newer than the import and is never flagged.
    """
    imported = parse_moment(imported_at)
    if imported is None:
        return sessions
    rekeyed = parse_moment(rekey.rekeyed_at) if rekey is not None else None
    flagged: list[AccountSession] = []
    for session in sessions:
        created = parse_moment(session.date_created)
        sellers = session.is_ours and session.kind is None
        older = (
            created is not None and created < imported and (rekeyed is None or created < rekeyed)
        )
        flagged.append(_with_predates(session, predates=sellers and older))
    return flagged


def with_own(audit: AccountSessionAudit, own: Mapping[str, str]) -> AccountSessionAudit:
    """``audit`` as the card shows it now: authorizations recorded since it was read are ours."""
    sessions = mark_own(audit.sessions, own)
    return audit.model_copy(
        update={
            "sessions": sessions,
            "foreign_count": sum(1 for session in sessions if not session.is_ours),
        }
    )
