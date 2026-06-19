"""Apply / Cancel footer for the edit-profile dialog tabs.

A small UI component the tab builders own — kept in its own file so the
render module stays under the aislop file-size budget. Each tab in
:mod:`_profile_dialog` instantiates one, then calls ``mark_dirty()`` /
``mark_clean()`` from change handlers; clicking Применить runs the
provided async handler with a Quasar ``loading`` spinner; clicking
Отмена runs the provided sync handler. Both reset to hidden after.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class _TabFooter:
    """Apply / Cancel pair shown at the bottom of an edit-profile tab.

    Hidden by default; ``mark_dirty()`` reveals it. The Применить button
    shows a Quasar ``loading`` spinner during its handler so the user gets
    immediate feedback that the apply is in flight — without this the
    dialog felt frozen during the (now-fast, but still non-zero) Telethon
    round-trip.
    """

    def __init__(
        self,
        *,
        apply: Callable[[], Awaitable[None]],
        cancel: Callable[[], None],
    ) -> None:
        self._apply_handler = apply
        self._cancel_handler = cancel
        with ui.row().classes("w-full justify-end gap-2") as self.row:
            self.cancel_btn = ui.button(
                "Отмена",
                icon="close",
                color="grey-7",
                on_click=self._on_cancel,
            ).props("flat")
            self.apply_btn = ui.button(
                "Применить",
                icon="check",
                color="primary",
                on_click=self._on_apply,
            )
        self.row.set_visibility(False)

    def mark_dirty(self) -> None:
        self.row.set_visibility(True)

    def mark_clean(self) -> None:
        self.row.set_visibility(False)

    async def _on_apply(self) -> None:
        self.apply_btn.props("loading")
        self.cancel_btn.disable()
        try:
            await self._apply_handler()
        finally:
            self.apply_btn.props(remove="loading")
            self.cancel_btn.enable()
        self.mark_clean()

    def _on_cancel(self) -> None:
        self._cancel_handler()
        self.mark_clean()
