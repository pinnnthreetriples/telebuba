"""Per-account warming pipeline rail — the 6-step cycle visual.

UI-thin per non-negotiable #1; every function carries ``# pragma: no cover``.
The pipeline reads only the polled ``WarmingAccountState`` (no new backend
calls, no new polling) and is rendered inside every warming-column kanban
card by ``features/warming/_board.py``.

The six steps are static (online → join → read → react → chat → sleep); the
``_active_step()`` resolver picks the *furthest step the cycle has reached*
from ``card.state`` and ``card.last_action`` (the loop advances ``last_action``
monotonically — see ``_loop._on_step`` — so the rail shows cycle progress, not
the instantaneous action). The active step pulses, its icon spins, and a
gradient connector flows from the last completed step into it. The detail panel
beneath shows channel/proxy/action data for that step; the summary bar at the
bottom shows cycle counters.

The rail is gated by the caller — ``_render_card`` only invokes
``render_cycle_pipeline`` when ``card.state != "idle"``, so idle-column cards
stay pixel-for-pixel identical to before.
"""

from __future__ import annotations

import dataclasses
import typing

from nicegui import ui

from features.warming._board_checks import _ru_event
from features.warming._board_styling import (
    _DETAIL_ICON_THEME,
    _PIPELINE_CONNECTOR_ACTIVE,
    _PIPELINE_CONNECTOR_DONE,
    _PIPELINE_CONNECTOR_PENDING,
    _PIPELINE_STEP_ACTIVE,
    _PIPELINE_STEP_DONE,
    _PIPELINE_STEP_ERROR,
    _PIPELINE_STEP_FLOOD,
    _PIPELINE_STEP_PENDING,
    _PIPELINE_STEP_QUAR,
    _PIPELINE_STEP_SLEEP,
    _relative_eta,
)

if typing.TYPE_CHECKING:
    from collections.abc import Callable

    from schemas.warming import WarmingAccountState


@dataclasses.dataclass(frozen=True, slots=True)
class _Step:  # pragma: no cover
    """Static metadata for one of the six warming pipeline steps.

    ``name`` is the canonical internal id (matches ``last_action`` values from
    ``services.warming``). ``label_ru`` is the short Russian caption shown on
    the rail / tooltip. ``icon`` is the unicode glyph rendered inside the
    step circle — emoji so it matches the rest of the warming card.
    """

    name: str
    label_ru: str
    icon: str


# ``name`` is the canonical internal id (mapped from ``last_action`` — load-
# bearing, do not rename). ``label_ru`` matches the spec C.3 rail captions
# (Подписка · Чтение · Сторис · Реакции · Пауза · Отчёт).
_CYCLE_STEPS: tuple[_Step, ...] = (
    _Step("online", "Подписка", "add_circle"),
    _Step("join", "Чтение", "chrome_reader_mode"),
    _Step("read", "Сторис", "auto_stories"),
    _Step("react", "Реакции", "thumb_up"),
    _Step("chat", "Пауза", "bedtime"),
    _Step("sleep", "Отчёт", "receipt_long"),
)

# Spec WMSG — the active-step strip description per rail index.
_STEP_DESC: tuple[str, ...] = (
    "Подписка на каналы (+3)",
    "Чтение ленты постов",
    "Просмотр сторис",
    "Поставлены реакции",
    "Пауза для естественности",
    "Отчёт по циклу сформирован",
)

# Maps ``last_action`` values emitted by ``services.warming`` to the 0-based
# index of the rail step. The loop advances ``last_action`` monotonically
# (``_loop._on_step``), so for an ``active`` card it is the *furthest step the
# cycle has reached* — not necessarily the instantaneous action — and for an
# ``error`` card it is the step that failed. Unknown values fall back to 0
# (online). ``read_or_react`` stays pinned to ``react`` (idx 3) for the error
# path, which still collapses read-then-react into that single token.
_ACTION_TO_STEP: dict[str, int] = {
    "set_online": 0,  # online
    "join": 1,  # join
    "read": 2,  # read
    "react": 3,  # react
    "read_or_react": 3,  # react (error path: collapsed read+react token)
    "send_dm": 4,  # chat
}
_ERROR_DETAIL_MAX_LEN: int = 60
_SLEEP_STEP_INDEX: int = 5

# Visual-state → Material glyph. "active" (and any unknown) fall back to the
# step's own topic icon via .get(), so the table only lists the overrides.
_STEP_GLYPH: dict[str, str] = {
    "done": "check",
    "pending": "remove",
    "error": "error",
    "flood": "timer",
    "quar": "block",
}
# Rail label colour by visual state; unknown (e.g. "pending") → muted (#A8A6A1).
# Spec: active #0066FF/600, done #12A150/500, inactive #A8A6A1/400.
_STEP_LABEL_CLS: dict[str, str] = {
    "active": "tbw-text-blue font-semibold",
    "done": "tbw-text-green font-medium",
    "error": "tbw-text-red",
    "flood": "tbw-text-amber",
    "quar": "tbw-text-orange",
}


def _next_active_index(card: WarmingAccountState) -> int:
    """Return the index of the furthest step reached given an ``active`` state.

    ``last_action`` advances monotonically through the cycle (see
    ``_loop._on_step``), so it names the furthest step reached; map it straight
    to its rail index. ``None``/unknown → 0 (online), so a just-started cycle
    lights up online. Clamped at ``_SLEEP_STEP_INDEX`` (5) defensively.
    """
    return min(_ACTION_TO_STEP.get(card.last_action or "", 0), _SLEEP_STEP_INDEX)


def _active_step(card: WarmingAccountState) -> tuple[int | None, str]:
    """Pick the index (0..5) and overall kind of the active step.

    Returns ``(None, "quar")`` when no step is currently active (quarantine
    halts the cycle entirely). The kind is independent of the index — it's
    the rail-wide "what flavour of activity is this?" marker (sleep / flood
    / error / quar), while the index points at which step is *live*.

    Resolution rules:
    - ``quarantine``  → ``(None, "quar")`` — entire rail dimmed.
    - ``error``       → ``(last-action-idx, "error")`` — the failing step is
                         pinned; if ``last_action`` is unknown, defaults to
                         ``online`` (idx 0) so the error has a step to land on.
    - ``flood_wait``  → ``(_SLEEP_STEP_INDEX, "flood")`` — engine paused on sleep step.
    - ``sleeping``    → ``(_SLEEP_STEP_INDEX, "sleep")`` — between-cycle cooldown.
    - ``active``      → ``(last-action-idx, "active")`` — the furthest step the
                         cycle has reached (``last_action`` advances
                         monotonically). Unknown / not-yet-started ``last_action``
                         falls back to idx 0 (online).
    - ``idle``        → ``(None, "active")`` — caller gates; defensive only.
    """
    if card.state == "quarantine":
        return (None, "quar")
    if card.state == "error":
        return (_ACTION_TO_STEP.get(card.last_action or "", 0), "error")
    if card.state in ("flood_wait", "sleeping"):
        kind = "flood" if card.state == "flood_wait" else "sleep"
        return (_SLEEP_STEP_INDEX, kind)
    if card.state == "active":
        return (_next_active_index(card), "active")
    return (None, "active")


def _error_tooltip(step: _Step, card: WarmingAccountState) -> str:  # pragma: no cover
    """Tooltip for an error step: truncated error detail or fallback text."""
    detail = (card.last_error or "").strip() or "без описания"
    if len(detail) > _ERROR_DETAIL_MAX_LEN:
        detail = detail[: _ERROR_DETAIL_MAX_LEN - 1] + "…"
    return f"Ошибка на «{step.label_ru}»: {detail}"


def _flood_tooltip(card: WarmingAccountState) -> str:  # pragma: no cover
    """Tooltip for a flood-wait step: remaining time or rate-limit notice."""
    remaining = _relative_eta(card.flood_wait_until)
    if remaining and remaining != "сейчас":
        return f"Flood-wait · ещё {remaining}"
    if card.flood_wait_seconds is not None:
        return f"Flood-wait · ещё {card.flood_wait_seconds} с"
    return "Flood-wait · Telegram ограничил аккаунт"


def _active_tooltip(step: _Step, card: WarmingAccountState) -> str:  # pragma: no cover
    """Tooltip for an active (live) step.

    The sleep step surfaces the next-run ETA; online does a health-check
    label; all other steps use the per-action Russian label + channel.
    """
    if step.name == "online":
        return "Проверка соединения с Telegram"
    if step.name == "sleep":
        eta = _relative_eta(card.next_run_at)
        return f"Сон до следующего цикла · {eta}" if eta else "Сон до следующего цикла"
    action_ru = {
        "join": "Подключение к каналу",
        "read": "Чтение сообщений",
        "react": "Реакция на сообщение",
        "chat": "Отправка сообщения",
    }.get(step.name, step.label_ru)
    channel = card.last_channel or "—"
    return f"{action_ru} · {channel}"


def _step_tooltip(step: _Step, card: WarmingAccountState, kind: str) -> str:  # pragma: no cover
    """Compose the per-step Russian tooltip text from the polled state.

    ``kind`` mirrors the visual class: ``done | active | pending | error |
    flood | quar``. The sleep step is special-cased to surface the next-run
    ETA so an operator knows exactly when the cycle resumes.
    """
    if kind in ("done", "pending"):
        suffix = "выполнен" if kind == "done" else "ожидает"
        return f"{step.label_ru} · {suffix}"
    if kind == "error":
        return _error_tooltip(step, card)
    if kind == "flood":
        return _flood_tooltip(card)
    if kind == "quar":
        return f"Карантинов: {card.quarantine_count}"
    return _active_tooltip(step, card)


def _connector_kind(left_idx: int, active_idx: int | None, kind: str) -> str:
    """Pick the styling for the connector between step ``left_idx`` and ``left_idx + 1``.

    - ``done``    — the step on the left is already past.
    - ``active``  — left step just finished; the connector flowing into the
                    active step lights up (animated gradient).
    - ``pending`` — both sides are still in the future.
    """
    if active_idx is None:
        return "pending"
    if left_idx < active_idx:
        if left_idx + 1 == active_idx and kind == "active":
            return "active"
        return "done"
    return "pending"


def _step_kind(idx: int, active_idx: int | None, kind: str) -> str:
    """Determine the visual state of step ``idx`` given the active resolution."""
    if kind == "quar":
        return "quar"
    if active_idx is None or idx > active_idx:
        return "pending"
    if idx < active_idx:
        return "done"
    return {"flood": "flood", "error": "error"}.get(kind, "active")


def render_cycle_pipeline(
    card: WarmingAccountState,
    status_line: Callable[[], None] | None = None,
) -> None:  # pragma: no cover
    """Top-level entry point — the spec C.3 «В прогреве» pipeline block.

    Caller gates idle cards (see ``_board._render_card``). The block is the
    pale-blue inset that stacks: a "Дней прогрева" header + the 42-segment
    zone bar, the 6-step rail, and the live active-step strip. ``status_line``
    (the legacy "what's happening now" row) is no longer rendered separately —
    the active-step strip subsumes it — but the parameter is kept so the
    caller-side wiring in ``_board`` does not change.
    """
    del status_line  # superseded by the active-step strip
    active_idx, kind = _active_step(card)
    with ui.element("div").classes("tbw-pipe w-full"):
        _render_zone_bar(card)
        _render_step_rail(card, active_idx, kind)
        _render_active_strip(card, active_idx, kind)


_ZONE_SEGMENTS = 42


def _render_zone_bar(card: WarmingAccountState) -> None:  # pragma: no cover
    """Render the "Дней прогрева" header + 42-segment gradient zone bar (spec C.3).

    Filled-segment count tracks ``progress_to_next`` (the card's already-
    quantised phase progress, so the bar does not flicker on the 4 s poll);
    the leading filled segment glows (``.tb-loadlead``) unless complete. The
    gradient endpoints rgb(5,117,230)→rgb(0,242,96) are applied per segment
    via an inline ``--i`` custom property so the row reads as one ramp.
    """
    days = card.warming_days
    target = None
    if days is not None and card.days_to_next_phase is not None:
        target = days + card.days_to_next_phase
    complete = card.phase == "warmed"
    progress = 1.0 if complete else (card.progress_to_next or 0.0)
    filled = max(0, min(_ZONE_SEGMENTS, round(progress * _ZONE_SEGMENTS)))

    with ui.row().classes("w-full items-baseline justify-between"):
        ui.label("Дней прогрева").classes("tbw-mini-label")
        days_txt = "—" if days is None else str(days)
        target_txt = f" / {target} дней" if target is not None else " дней"
        ui.label(f"{days_txt}{target_txt}").classes("tbw-mini-value")

    with ui.row().classes("tbw-zone w-full"):
        for i in range(_ZONE_SEGMENTS):
            on = i < filled
            lead = on and i == filled - 1 and not complete
            cls = "tbw-seg tbw-seg-on" if on else "tbw-seg"
            if lead:
                cls += " tb-loadlead"
            ui.element("div").classes(cls).style(f"--i:{i / (_ZONE_SEGMENTS - 1)}")


def _render_step_rail(  # pragma: no cover
    card: WarmingAccountState,
    active_idx: int | None,
    kind: str,
) -> None:
    """The 6-step horizontal rail with connectors between them (spec C.3).

    Compact spec nodes: done = green check, active = blue live dot, inactive =
    small white dot with a grey hairline border. Connectors are a 2px track
    (#DCE2EC) with green-done / blue-active fills.
    """
    connector_cls = {
        "done": _PIPELINE_CONNECTOR_DONE,
        "active": _PIPELINE_CONNECTOR_ACTIVE,
        "pending": _PIPELINE_CONNECTOR_PENDING,
    }
    step_cls = {
        "done": _PIPELINE_STEP_DONE,
        "active": _PIPELINE_STEP_ACTIVE,
        "pending": _PIPELINE_STEP_PENDING,
        "error": _PIPELINE_STEP_ERROR,
        "flood": _PIPELINE_STEP_FLOOD,
        "quar": _PIPELINE_STEP_QUAR,
    }
    with ui.row().classes("w-full items-start gap-0 pt-2"):
        for idx, step in enumerate(_CYCLE_STEPS):
            if idx > 0:
                left_kind = _connector_kind(idx - 1, active_idx, kind)
                ui.element("div").classes(
                    f"tbw-conn flex-1 {connector_cls[left_kind]}",
                ).style("margin-top: 6px")
            sk = _step_kind(idx, active_idx, kind)
            cls = step_cls[sk]
            # A resting "sleep" node maps to the active slot but gets a calm
            # blue treatment (no pulse) instead of the energetic blue glow.
            if sk == "active" and kind == "sleep":
                cls = _PIPELINE_STEP_SLEEP
            tooltip = _step_tooltip(step, card, sk)
            # Done nodes show a white check; error/flood/quar show their glyph.
            # Active / pending dots carry no glyph (spec dot, not an icon).
            glyph = _STEP_GLYPH.get(sk) if sk in _STEP_GLYPH else None
            with ui.column().classes("items-center gap-1 shrink-0"):
                circle = ui.element("div").classes(f"tbw-node {cls}")
                if glyph is not None:
                    with circle:
                        ui.icon(glyph).classes("text-[11px]")
                circle.tooltip(tooltip)
                label_cls = _STEP_LABEL_CLS.get(sk, "tbw-text-faint")
                ui.label(step.label_ru).classes(f"tbw-step-label {label_cls}")


def _render_active_strip(
    card: WarmingAccountState,
    active_idx: int | None,
    kind: str,
) -> None:  # pragma: no cover
    """The live active-step strip below the rail (spec C.3).

    For a running cycle this is the pale-blue (#EEF4FF) strip with a pulsing
    live dot + the per-step WMSG description. For the off-nominal kinds
    (sleep / flood / quarantine / error) it keeps the one informative line an
    operator needs, tinted to the matching state colour.
    """
    if kind == "active" and active_idx is not None:
        text = (
            _STEP_DESC[active_idx]
            if active_idx < len(_STEP_DESC)
            else _CYCLE_STEPS[active_idx].label_ru
        )
        with ui.row().classes("tbw-active-strip w-full items-center gap-2"):
            ui.element("div").classes("tbw-dot-blue tb-livedot shrink-0")
            ui.label(text).classes("tbw-active-text tb-pulse")
        return

    detail = _off_nominal_detail(card, active_idx, kind)
    if detail is None:
        return
    icon_name, text = detail
    icon_bg, icon_color = _DETAIL_ICON_THEME.get(kind, ("tbw-tile-gray", "tbw-text-muted"))
    text_cls = {
        "flood": "tbw-text-amber",
        "quar": "tbw-text-orange",
        "error": "tbw-text-red",
        "sleep": "tbw-text-muted",
    }.get(kind, "tbw-text-blue")
    with ui.row().classes(f"tbw-detail-strip {kind} w-full items-center gap-2"):
        with ui.element("div").classes(f"tbw-detail-tile {icon_bg}"):
            ui.icon(icon_name).classes(f"text-sm {icon_color}")
        ui.label(text).classes(f"tbw-detail-text {text_cls}")


def _off_nominal_detail(  # noqa: PLR0911 - flat state→line dispatch reads clearer as early returns
    card: WarmingAccountState,
    active_idx: int | None,
    kind: str,
) -> tuple[str, str] | None:  # pragma: no cover
    """One (icon, text) line summarising a non-running cycle state, or None."""
    if kind == "error" and card.last_error:
        err = card.last_error
        if len(err) > _ERROR_DETAIL_MAX_LEN:
            err = err[: _ERROR_DETAIL_MAX_LEN - 1] + "…"
        return ("error", err)
    if active_idx is None:
        if kind == "quar":
            return ("block", f"Карантин · случаев: {card.quarantine_count}")
        return None
    if active_idx == _SLEEP_STEP_INDEX and kind == "sleep":
        eta = _relative_eta(card.next_run_at)
        return ("bedtime", f"Сон · пробуждение через {eta}" if eta else "Сон до следующего цикла")
    if active_idx == _SLEEP_STEP_INDEX and kind == "flood":
        return ("timer", _flood_tooltip(card))
    if card.last_event:
        return ("bolt", _ru_event(card.last_event))
    if card.last_channel:
        return ("tag", f"Канал: {card.last_channel}")
    return None
