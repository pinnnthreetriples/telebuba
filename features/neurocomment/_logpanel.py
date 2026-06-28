"""Neurocomment engine logs — a collapsible dark "terminal" panel (round-2 fixes).

Mirrors the warming card's «Логи аккаунта» (`features/warming/_termlog.py`) but for
the fleet-wide neurocomment engine: one collapsible panel under the engine card
showing recent ``neurocomment_*`` events newest-first. UI-thin per non-negotiable #1
— renderers carry ``# pragma: no cover``; the pure ``nc_event_label`` / ``nc_log_detail``
helpers carry the only logic and are unit-tested in ``tests/features/test_neurocomment_labels.py``.

It does not import ``features/warming`` (isolation #1): the dark-terminal CSS is
re-stated under a ``tb-nc-log`` prefix in ``__init__._NC_CSS``, and the event map is
neurocomment-specific. Logs are scoped via ``LogFilter(event_prefix="neurocomment")``
(neurocomment listener/sweep rows are not account-scoped, so the warming per-account
filter would miss them).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from nicegui import ui

from core.config import settings
from schemas.logs import LogFilter
from services.logs import load_logs_page

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from schemas.logs import LogEntry

# event name → (Material icon, Russian label). Keyed by the events the engine /
# runtime / onboarding emit (verified in services/neurocomment/**). Unknown events
# humanise via ``nc_event_label``'s fallback.
_NC_EVENT_LABEL: dict[str, tuple[str, str]] = {
    "neurocomment_posted": ("send", "Комментарий отправлен"),
    "neurocomment_post_skipped": ("skip_next", "Пост пропущен"),
    "neurocomment_no_account_available": ("person_off", "Нет свободного аккаунта"),
    "neurocomment_no_campaign": ("inbox", "Нет кампании для канала"),
    "neurocomment_channel_cooled": ("ac_unit", "Канал на паузе"),
    "neurocomment_generation_exhausted": ("auto_awesome", "Не сгенерировал комментарий"),
    "neurocomment_post_cooldown": ("timer", "Лимит — пауза аккаунта"),
    "neurocomment_post_gated": ("block", "Блок записи в канале"),
    "neurocomment_post_failed": ("error", "Комментарий не ушёл"),
    "neurocomment_challenge_backoff": ("smart_toy", "Капча — пауза канала"),
    "neurocomment_channel_backoff": ("delete_sweep", "Удаления — пауза канала"),
    "neurocomment_runtime_reconciled": ("sync", "Слушатель пересобран"),
    "neurocomment_listener_join_failed": ("link_off", "Слушатель не вступил"),
    "neurocomment_sweep_failed": ("warning", "Сбой контроля удалений"),
    "neurocomment_sweep_read_failed": ("warning", "Не прочитал комментарии"),
    "neurocomment_onboard_retry_later": ("schedule", "Онбординг отложен"),
    "neurocomment_onboard_resolve_failed": ("error", "Не нашёл обсуждение"),
    "neurocomment_onboard_pair_failed": ("error", "Онбординг не удался"),
    "neurocomment_onboard_spam_probe_failed": ("warning", "Спам-проверка не удалась"),
    "neurocomment_pipeline_failed": ("error", "Сбой обработки поста"),
}

_NC_LOG_ACCENT: dict[str, str] = {
    "success": "tb-nc-log-ok",
    "warning": "tb-nc-log-warn",
    "error": "tb-nc-log-err",
}
_DETAIL_MAX_LEN = 80


@dataclasses.dataclass
class LogPanelState:
    """Collapsible-panel state, kept across the engine panel's poll re-renders."""

    expanded: bool = False
    entries: list[LogEntry] | None = None
    sig: tuple[int, ...] = ()


def nc_event_label(event: str) -> tuple[str, str]:
    """Map a neurocomment event to ``(material_icon, russian_label)``.

    Unknown events fall back to a humanised form of the raw name (minus the
    ``neurocomment_`` prefix) so a newly-added event still reads cleanly.
    """
    known = _NC_EVENT_LABEL.get(event)
    if known is not None:
        return known
    return ("circle", event.removeprefix("neurocomment_").replace("_", " "))


def nc_log_detail(event: str, extra: Mapping[str, object]) -> str:
    """Plain-Russian one-line detail for a neurocomment log row (operator-facing).

    ``neurocomment_channel_backoff`` carries both ``missing`` and ``cooldown_seconds``,
    so the parts compose («удалено N · пауза M с») rather than one shadowing the other.
    """
    channel = extra.get("channel")
    if event == "neurocomment_post_skipped" and extra.get("reason"):
        return f"{channel}: {extra['reason']}" if channel else str(extra["reason"])
    parts: list[str] = [str(channel)] if channel else []
    if extra.get("missing") is not None:
        parts.append(f"удалено {extra['missing']}")
    cooldown = extra.get("cooldown_seconds")
    if isinstance(cooldown, (int, float)):
        parts.append(f"пауза {int(cooldown)} с")
    if len(parts) == (1 if channel else 0):
        # No deletion/cooldown specifics — fall back to status / error / message.
        parts.extend(
            str(value)
            for value in (extra.get("status"), extra.get("error_type"), extra.get("message"))
            if value
        )
    text = " · ".join(parts)
    return text[: _DETAIL_MAX_LEN - 1] + "…" if len(text) > _DETAIL_MAX_LEN else text


async def refresh_logs(state: LogPanelState) -> bool:  # pragma: no cover
    """Fetch recent neurocomment rows; return True only when the visible set changed."""
    page = await load_logs_page(
        LogFilter(event_prefix="neurocomment", limit=settings.neurocomment.log_limit),
    )
    new_sig = tuple(entry.id for entry in page.entries)
    if state.sig == new_sig:
        return False
    state.sig = new_sig
    state.entries = page.entries
    return True


def render_log_panel(
    state: LogPanelState,
    on_toggle: Callable[[], Awaitable[None]],
) -> None:  # pragma: no cover
    """Collapsible «Лог событий» header + dark terminal body (when open)."""
    chevron = "expand_more" if state.expanded else "chevron_right"
    count = len(state.entries) if state.entries else 0
    header = (
        ui.row()
        .classes(
            "w-full items-center gap-2 cursor-pointer select-none flex-nowrap",
        )
        .style("margin-top:12px;padding-top:12px;border-top:1px solid #E4ECFA")
    )
    with header:
        ui.html(
            '<span class="pl-pulse" style="width:8px;height:8px;border-radius:50%;'
            'background:#0066FF;flex-shrink:0"></span>',
        )
        ui.label("Лог событий").classes("tb-title")
        ui.html(
            f'<span style="font-size:11px;font-weight:600;background:#F2F1EE;color:#74726E;'
            f'border-radius:9999px;padding:2px 9px">{count}</span>',
        )
        ui.element("div").style("flex:1")
        ui.icon(chevron).style("color:#9A9893")
    header.on("click", on_toggle)
    if not state.expanded:
        return
    with ui.element("div").classes("tb-nc-log w-full").style("margin-top:10px"):
        if state.entries is None:
            ui.label("Загрузка…").classes("tb-nc-log-empty")
        elif not state.entries:
            ui.label("Событий пока нет").classes("tb-nc-log-empty")
        else:
            for entry in state.entries:
                _render_log_row(entry)


def _render_log_row(entry: LogEntry) -> None:  # pragma: no cover
    accent = _NC_LOG_ACCENT.get(entry.status, "")
    icon, label = nc_event_label(entry.event)
    label_cls = "tb-nc-log-msg" if entry.status == "success" else f"tb-nc-log-msg {accent}"
    with ui.row().classes("tb-nc-log-row w-full"):
        ui.label(entry.created_at[11:19]).classes("tb-nc-log-time")
        ui.icon(icon).classes(f"text-sm {accent}")
        ui.label(label).classes(label_cls)
        detail = nc_log_detail(entry.event, entry.extra)
        if detail:
            ui.label(detail).classes("tb-nc-log-kv")
