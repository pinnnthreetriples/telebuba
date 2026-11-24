"""The pool's typed failures — split out of ``_pool`` for the file-size budget.

Re-exported by ``_pool``, so ``from core.telegram_client._pool import ...`` is unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.telegram_client._client import DeviceUnavailableError


class TelegramClientPoolError(RuntimeError):
    """Raised when the pool fails to (re)connect a client.

    Transport failures (``OSError``) raise after Telethon's own connect ladder;
    other faults get one fresh attempt first.
    """

    def __init__(self, account_id: str, cause: Exception) -> None:
        super().__init__(f"telegram pool connect failed for {account_id}: {cause}")
        self.account_id = account_id
        self.cause = cause


class TelegramClientUnavailableError(TelegramClientPoolError):
    """The POOL refused to serve the account — shutdown, or a live tombstone.

    Distinct from its parent because nothing was learned about Telegram or the
    session here: no connect was attempted, and ``cause`` is the pool's own
    ``RuntimeError`` rather than a Telethon / transport failure. The probe paths
    have to tell the two apart — ``_session._probe_client`` unwraps ``cause`` so
    the real error reaches its classification ladder, and a bare ``RuntimeError``
    matched no arm there, so a login in flight wrote a sticky ``unknown_error``
    onto a row whose session was fine.

    A subclass, so every ``except TelegramClientPoolError`` borrower
    (``_actions``, ``_read``) keeps classifying it as an unavailable account.
    """


class TelegramDeviceUnavailableError(TelegramClientUnavailableError):
    """The requested device cannot be served — and is never substituted by the other.

    ``code`` is the stable :data:`schemas.devices.DeviceRefusalCode`.
    """

    def __init__(self, account_id: str, cause: DeviceUnavailableError) -> None:
        super().__init__(account_id, cause)
        self.code = cause.code


class SessionFileMissingError(TelegramClientUnavailableError):
    """The account's ``.session`` file is gone, or holds no key: refused before Telethon runs.

    Telethon would create an empty database at the path and write a fresh, unregistered key
    into it, which reads as a key Telegram revoked. ``state`` is ``core.session_file``'s
    verdict (``missing`` / ``no_key`` / ``not_a_session``). Nothing was learned about Telegram
    or the key, so every
    ``except TelegramClientPoolError`` borrower still classifies it as an unavailable account;
    only the session check tells it apart.
    """

    def __init__(self, account_id: str, state: str) -> None:
        super().__init__(account_id, FileNotFoundError(state))
        # Not "connect failed": nothing was tried.
        self.args = (f"session file {state} for {account_id}",)
        self.state = state
