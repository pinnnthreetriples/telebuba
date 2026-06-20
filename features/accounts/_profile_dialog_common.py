"""Shared primitives for the edit-profile dialog modules.

``_DialogRefs`` (the element-handle bag), the data-URL encoder, and the dead-
client tracking live here so that :mod:`_profile_dialog_render` and
:mod:`_profile_dialog_photos` can both import them without forming an import
cycle. ``register_disconnect_tracker`` is wired once from :mod:`main`.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

from nicegui import app, ui

if TYPE_CHECKING:
    import asyncio

    from schemas.accounts import AccountProfileSnapshot


# Client ids whose websocket dropped. ``app.on_disconnect`` populates this once
# wired via ``register_disconnect_tracker()`` at app startup. Optimistic
# updates and ``_apply_snapshot`` consult it before mutating UI elements so
# they don't surface "Client has been deleted" warnings on detached clients.
_DEAD_CLIENTS: set[str] = set()


def register_disconnect_tracker() -> None:
    """Wire ``app.on_disconnect`` once at startup to feed ``_DEAD_CLIENTS``."""

    def _on_disconnect(client: object) -> None:
        client_id = getattr(client, "id", None)
        if isinstance(client_id, str):
            _DEAD_CLIENTS.add(client_id)

    app.on_disconnect(_on_disconnect)


class _DialogRefs:
    """Element handles the background snapshot loader writes into.

    Attributes are wired up in ``_open_profile_dialog`` as the elements get
    created — declared here only so type checkers can see the shape.

    ``account_id`` is the Telegram account behind the open dialog — needed
    by the music-row and photo-card delete buttons so the click handler can
    call into the service without re-threading account_id through every render
    call.

    ``current_snapshot`` holds the latest applied snapshot so optimistic
    update helpers (``_apply_optimistic_*``) can mutate-and-rerender without
    a Telegram round-trip after each upload.

    ``client_id`` ties the dialog to the originating NiceGUI client so a
    global ``app.on_disconnect`` handler can flag this exact dialog dead
    without freezing other open tabs. ``closed`` is the same idea for the
    Quasar-side ``dialog.on('hide')`` event — flipped synchronously so the
    apply path can short-circuit before mutating detached elements.
    """

    first_name: ui.input
    last_name: ui.input
    username: ui.input
    bio: ui.textarea
    avatar_slot: ui.element
    identity_slot: ui.element
    photo_preview_container: ui.element
    stories_container: ui.element
    music_section: ui.element
    music_list_container: ui.element
    sync_label: ui.label
    refresh_button: ui.button
    error_banner: ui.label
    initial_load_task: asyncio.Task[None]
    account_id: str
    current_snapshot: AccountProfileSnapshot | None
    client_id: str
    closed: bool


def _is_client_dead(refs: _DialogRefs) -> bool:
    return refs.closed or refs.client_id in _DEAD_CLIENTS


def _avatar_data_url(image_bytes: bytes | None) -> str | None:
    if not image_bytes:
        return None
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"
